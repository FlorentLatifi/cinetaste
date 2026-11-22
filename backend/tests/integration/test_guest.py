"""Guest mode: the onboarding deck and a ranked slate with no account.

The promise is "nothing about you is stored", so beyond the happy path these
check the tables a signed-in flow would write to stay empty.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infrastructure.db.models.interaction import (
    InteractionEvent,
    RecommendationImpression,
    UserTitleState,
)
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.models.user import User
from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_guest_cards_need_no_account(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    await seed_catalog(db_session)

    res = await client.get(f"{api_prefix}/guest/cards", params={"limit": 12})
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 12
    assert all(item["poster_url"] for item in items)

    # Served from the cache the second time; same deck either way.
    again = await client.get(f"{api_prefix}/guest/cards", params={"limit": 12})
    assert [i["id"] for i in again.json()["items"]] == [i["id"] for i in items]

    # Excluding what was shown pages through the rest of the catalog.
    shown = [i["id"] for i in items]
    more = await client.get(
        f"{api_prefix}/guest/cards", params=[("limit", "8"), *(("exclude", i) for i in shown)]
    )
    assert more.status_code == 200, more.text
    assert not set(shown) & {i["id"] for i in more.json()["items"]}


async def test_guest_slate_is_ranked_explained_and_stores_nothing(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session)
    thrillers = [t for t in titles if t.name.endswith(("00", "06", "12"))]
    comedy = next(t for t in titles if t.name.endswith("01"))
    unseen = next(t for t in titles if t.name.endswith("02"))

    reactions = [
        {"title_id": str(thrillers[0].id), "action": "rate_4"},
        {"title_id": str(thrillers[1].id), "action": "rate_3"},
        {"title_id": str(comedy.id), "action": "rate_1"},
        {"title_id": str(unseen.id), "action": "haven't_seen"},
    ]
    res = await client.post(
        f"{api_prefix}/guest/recommendations", json={"reactions": reactions, "limit": 8}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"]
    assert 0 < len(items) <= 8
    assert body["slate_id"] is None

    answered = {r["title_id"] for r in reactions}
    assert not answered & {i["title"]["id"] for i in items}, "answered cards came back"
    assert all(i["reasons"] for i in items), "every pick explains itself"
    # The stored-profile path cites liked titles; the guest path must too.
    messages = " ".join(r["message"] for i in items for r in i["reasons"])
    assert thrillers[0].name in messages or thrillers[1].name in messages

    for model in (User, InteractionEvent, UserTitleState, TasteProfile, RecommendationImpression):
        assert await _count(db_session, model) == 0, f"{model.__name__} was written"


async def test_guest_gates_count_only_real_ratings(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"

    too_few = await client.post(
        url,
        json={
            "reactions": [
                {"title_id": str(titles[0].id), "action": "rate_4"},
                {"title_id": str(titles[1].id), "action": "rate_3"},
                {"title_id": str(titles[2].id), "action": "haven't_seen"},
            ]
        },
    )
    assert too_few.status_code == 400
    assert too_few.json()["code"] == "guest_insufficient_ratings"

    # Ratings of titles we don't have shape nothing, so they don't count.
    ghosts = await client.post(
        url,
        json={
            "reactions": [
                {"title_id": str(titles[0].id), "action": "rate_4"},
                {"title_id": str(uuid4()), "action": "rate_4"},
                {"title_id": str(uuid4()), "action": "rate_4"},
            ]
        },
    )
    assert ghosts.status_code == 400
    assert ghosts.json()["code"] == "guest_insufficient_ratings"

    # Answering the same card twice counts once.
    repeated = await client.post(
        url,
        json={
            "reactions": [
                {"title_id": str(titles[0].id), "action": "rate_4"},
                {"title_id": str(titles[0].id), "action": "rate_3"},
                {"title_id": str(titles[1].id), "action": "rate_4"},
            ]
        },
    )
    assert repeated.status_code == 400
    assert repeated.json()["code"] == "guest_insufficient_ratings"

    all_negative = await client.post(
        url,
        json={
            "reactions": [
                {"title_id": str(t.id), "action": "rate_1"} for t in titles[:4]
            ]
        },
    )
    assert all_negative.status_code == 400
    assert all_negative.json()["code"] == "guest_insufficient_positive"


async def test_guest_request_is_bounded(client: AsyncClient, api_prefix: str) -> None:
    too_many = [{"title_id": str(uuid4()), "action": "rate_4"} for _ in range(81)]
    res = await client.post(f"{api_prefix}/guest/recommendations", json={"reactions": too_many})
    assert res.status_code == 422

    big_slate = await client.post(
        f"{api_prefix}/guest/recommendations",
        json={"reactions": too_many[:3], "limit": 31},
    )
    assert big_slate.status_code == 422


# --------------------------------------------------------- caching, pooling, concurrency


def _reactions(titles) -> list[dict[str, str]]:  # noqa: ANN001
    return [
        {"title_id": str(titles[0].id), "action": "rate_4"},
        {"title_id": str(titles[6].id), "action": "rate_3"},
        {"title_id": str(titles[1].id), "action": "rate_1"},
        {"title_id": str(titles[2].id), "action": "haven't_seen"},
    ]


async def test_identical_answers_are_served_from_cache_with_the_same_body(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from app.application.recommendation_service import RecommendationService

    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 8}

    ranked_calls = 0
    real = RecommendationService.guest_slate

    async def counting(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal ranked_calls
        ranked_calls += 1
        return await real(self, *args, **kwargs)

    with patch.object(RecommendationService, "guest_slate", counting):
        first = await client.post(url, json=body)
        again = await client.post(url, json=body)
        # Same answers spelled differently (legacy action name, a repeated card) are the
        # same input once normalised, so they hit the same entry.
        respelled = {
            "reactions": [
                {"title_id": str(titles[0].id), "action": "rate_1"},  # superseded below
                *_reactions(titles),
            ],
            "limit": 8,
        }
        third = await client.post(url, json=respelled)

        assert first.status_code == again.status_code == third.status_code == 200
        assert ranked_calls == 1, "identical answers must not be ranked again"
        assert again.json() == first.json() == third.json()

        # A different limit, or different answers, is a different slate: ranked afresh.
        await client.post(url, json={**body, "limit": 9})
        assert ranked_calls == 2
        other = [{**body["reactions"][0], "action": "rate_3"}, *body["reactions"][1:]]
        await client.post(url, json={"reactions": other, "limit": 8})
        assert ranked_calls == 3

    for model in (User, InteractionEvent, UserTitleState, TasteProfile, RecommendationImpression):
        assert await _count(db_session, model) == 0, f"{model.__name__} was written"


async def test_cached_slate_equals_a_freshly_ranked_one(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from app.infrastructure.cache import reset_cache_for_tests

    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 10}

    fresh = await client.post(url, json=body)
    cached = await client.post(url, json=body)
    await reset_cache_for_tests()
    reranked = await client.post(url, json=body)
    assert fresh.json() == cached.json() == reranked.json()


async def test_failed_requests_are_never_cached(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    too_few = {"reactions": _reactions(titles)[:2]}
    assert (await client.post(url, json=too_few)).status_code == 400
    assert (await client.post(url, json=too_few)).status_code == 400  # still refused, not "served"


async def test_cache_can_be_turned_off(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from app.api.deps import get_settings_dep
    from app.application.recommendation_service import RecommendationService
    from app.main import app

    titles = await seed_catalog(db_session)
    settings = get_settings().model_copy(update={"guest_slate_cache_ttl_seconds": 0})
    app.dependency_overrides[get_settings_dep] = lambda: settings
    calls = 0
    real = RecommendationService.guest_slate

    async def counting(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        return await real(self, *a, **k)

    try:
        with patch.object(RecommendationService, "guest_slate", counting):
            body = {"reactions": _reactions(titles), "limit": 8}
            await client.post(f"{api_prefix}/guest/recommendations", json=body)
            await client.post(f"{api_prefix}/guest/recommendations", json=body)
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
    assert calls == 2


async def test_guest_ranking_is_capped_and_refuses_with_retry_after(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from app.api.deps import get_settings_dep
    from app.application.recommendation_service import RecommendationService
    from app.main import app

    titles = await seed_catalog(db_session)
    settings = get_settings().model_copy(
        update={"guest_rank_max_concurrent": 1, "guest_rank_max_wait_seconds": 0.1}
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings

    entered, release = asyncio.Event(), asyncio.Event()
    real = RecommendationService.guest_slate

    async def slow(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        entered.set()
        await release.wait()
        return await real(self, *a, **k)

    url = f"{api_prefix}/guest/recommendations"
    first_body = {"reactions": _reactions(titles), "limit": 8}
    # Different answers: the cache must not be what saves the second request.
    second_body = {
        "reactions": [
            {"title_id": str(titles[3].id), "action": "rate_4"},
            {"title_id": str(titles[4].id), "action": "rate_3"},
            {"title_id": str(titles[5].id), "action": "rate_2"},
        ],
        "limit": 8,
    }
    try:
        with patch.object(RecommendationService, "guest_slate", slow):
            running = asyncio.create_task(client.post(url, json=first_body))
            await asyncio.wait_for(entered.wait(), timeout=5)

            # Run it as a task and bound the wait without cancelling: without a cap this
            # request would sit on the held slot forever, and cancelling a request
            # mid-flight leaks its DB connection into the next test. A regression
            # should fail this one test, not hang the suite.
            second = asyncio.create_task(client.post(url, json=second_body))
            done, _pending = await asyncio.wait({second}, timeout=10)
            if not done:
                release.set()  # let it finish cleanly, then report
                await second
                pytest.fail("a second guest slate was admitted while the only slot was taken")
            refused = second.result()
            assert refused.status_code == 503, refused.text
            assert refused.json()["code"] == "guest_busy"
            assert int(refused.headers["Retry-After"]) >= 1

            # Not a cost for anyone else: cards and the rest of the API still answer.
            assert (await client.get(f"{api_prefix}/guest/cards", params={"limit": 8})).status_code == 200

            release.set()
            assert (await running).status_code == 200

        # Capacity came back: the refused request now succeeds.
        assert (await client.post(url, json=second_body)).status_code == 200
    finally:
        release.set()
        app.dependency_overrides.pop(get_settings_dep, None)


async def test_slate_is_the_same_with_and_without_the_shared_popular_pool(
    db_session: AsyncSession,
) -> None:
    from app.application.onboarding_service import normalize_reactions
    from app.application.popular_pool import PopularPool
    from app.application.recommendation_service import RecommendationService
    from app.infrastructure.db.session import async_session_factory

    titles = await seed_catalog(db_session)
    settings = get_settings()
    normalized = normalize_reactions(_reactions(titles))
    pool = PopularPool(async_session_factory, ttl_seconds=300)

    def shape(ranked):  # noqa: ANN001, ANN202
        return [
            (t.id, item.score, [(r.code, r.message) for r in item.reasons], sorted(g.name for g in t.genres))
            for t, item in ranked
        ]

    async with async_session_factory() as session:
        plain = await RecommendationService(session, settings).guest_slate(normalized, limit=12)
    for _ in range(2):  # the second run is served from the pool
        async with async_session_factory() as session:
            pooled = await RecommendationService(session, settings, popular_pool=pool).guest_slate(
                normalized, limit=12
            )
        assert shape(pooled) == shape(plain)
    assert pool.loads == 1


async def test_pooled_titles_stay_readable_after_a_request_rolls_back(
    db_session: AsyncSession,
) -> None:
    """Why the pool loads in its own session: a rollback elsewhere must not expire them."""
    from app.application.popular_pool import PopularPool
    from app.infrastructure.db.session import async_session_factory

    await seed_catalog(db_session)
    pool = PopularPool(async_session_factory, ttl_seconds=300)
    popular = await pool.get(10)
    assert popular

    async with async_session_factory() as request_session:
        await request_session.rollback()  # what get_db does when a request fails
    for title in popular:
        assert title.embedding is not None
        assert title.name
        assert all(g.name for g in title.genres)


# ------------------------------------------------------------------- timing log


def _timing_lines(caplog) -> list[dict[str, str]]:  # noqa: ANN001
    lines = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith(("guest_slate_timing", "guest_cards_timing")):
            lines.append({"event": message.split()[0], **dict(p.split("=", 1) for p in message.split()[1:])})
    return lines


async def test_slate_requests_log_where_the_time_went(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, caplog
) -> None:  # noqa: ANN001
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 8}

    with caplog.at_level(logging.INFO, logger="cinetaste.guest.timing"):
        miss = await client.post(url, json=body, headers={"x-request-id": "req-miss"})
        hit = await client.post(url, json=body, headers={"x-request-id": "req-hit"})
        refused = await client.post(url, json={"reactions": _reactions(titles)[:2]})
        await client.get(f"{api_prefix}/guest/cards", params={"limit": 8})
    assert miss.status_code == hit.status_code == 200 and refused.status_code == 400

    slate = [line for line in _timing_lines(caplog) if line["event"] == "guest_slate_timing"]
    assert [line["outcome"] for line in slate] == ["miss", "hit", "rejected"]

    ranked = slate[0]
    assert ranked["request_id"] == "req-miss"
    for key in ("total_ms", "cache_ms", "gate_wait_ms", "validate_ms", "profile_ms",
                "candidates_ms", "rank_ms", "serialize_ms", "db_ms", "db_max_ms"):
        assert key in ranked, f"missing {key}"
    assert int(ranked["db_statements"]) >= 5, "statements are counted through the real engine"
    assert float(ranked["db_ms"]) > 0
    assert ranked["pool"] == "loaded"
    assert ranked["limit"] == "8" and ranked["reactions"] == "4"

    cached = slate[1]
    assert cached["request_id"] == "req-hit"
    assert "rank_ms" not in cached and "gate_wait_ms" not in cached, "a hit does no ranking"
    assert int(cached["db_statements"]) <= 3  # re-reading titles + genres only

    assert any(line["event"] == "guest_cards_timing" for line in _timing_lines(caplog))


async def test_a_second_miss_reports_the_popular_pool_served_from_memory(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, caplog
) -> None:  # noqa: ANN001
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    first = {"reactions": _reactions(titles), "limit": 8}
    other = {"reactions": [{**_reactions(titles)[0], "action": "rate_3"}, *_reactions(titles)[1:]], "limit": 8}
    with caplog.at_level(logging.INFO, logger="cinetaste.guest.timing"):
        await client.post(url, json=first)
        await client.post(url, json=other)
    pools = [line["pool"] for line in _timing_lines(caplog) if line["event"] == "guest_slate_timing"]
    assert pools == ["loaded", "memory"]


async def test_a_refused_request_still_logs_its_wait(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, caplog
) -> None:  # noqa: ANN001
    from app.api.deps import get_settings_dep
    from app.application.recommendation_service import RecommendationService
    from app.main import app

    titles = await seed_catalog(db_session)
    settings = get_settings().model_copy(
        update={"guest_rank_max_concurrent": 1, "guest_rank_max_wait_seconds": 0.1}
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    entered, release = asyncio.Event(), asyncio.Event()
    real = RecommendationService.guest_slate

    async def slow(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        entered.set()
        await release.wait()
        return await real(self, *a, **k)

    url = f"{api_prefix}/guest/recommendations"
    second_body = {
        "reactions": [
            {"title_id": str(titles[3].id), "action": "rate_4"},
            {"title_id": str(titles[4].id), "action": "rate_3"},
            {"title_id": str(titles[5].id), "action": "rate_2"},
        ],
        "limit": 8,
    }
    try:
        with (
            patch.object(RecommendationService, "guest_slate", slow),
            caplog.at_level(logging.INFO, logger="cinetaste.guest.timing"),
        ):
            running = asyncio.create_task(client.post(url, json={"reactions": _reactions(titles), "limit": 8}))
            await asyncio.wait_for(entered.wait(), timeout=5)
            second = asyncio.create_task(client.post(url, json=second_body))
            done, _ = await asyncio.wait({second}, timeout=10)
            release.set()
            await running
            if not done:
                await second
                pytest.fail("a second guest slate was admitted while the only slot was taken")
    finally:
        release.set()
        app.dependency_overrides.pop(get_settings_dep, None)

    busy = [line for line in _timing_lines(caplog) if line.get("outcome") == "busy"]
    assert len(busy) == 1
    assert float(busy[0]["gate_wait_ms"]) >= 90, "the time spent queueing is what the log is for"
    assert "rank_ms" not in busy[0]


# ------------------------------------------------------------ cards single-flight


def _slow_cards(calls: list[dict], *, fail_first: bool = False):  # noqa: ANN202
    """Wrap OnboardingService.cards: record calls, take long enough to overlap."""
    from app.application.onboarding_service import OnboardingService
    from app.domain.exceptions import AppError

    real = OnboardingService.cards

    async def wrapped(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        calls.append(kwargs)
        await asyncio.sleep(0.2)
        if fail_first and len(calls) == 1:
            raise AppError("deck unavailable", status_code=503, code="deck_down")
        return await real(self, *args, **kwargs)

    return patch.object(OnboardingService, "cards", wrapped)


async def test_a_cold_burst_of_identical_cards_requests_builds_the_deck_once(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, caplog
) -> None:  # noqa: ANN001
    await seed_catalog(db_session)
    calls: list[dict] = []
    with _slow_cards(calls), caplog.at_level(logging.INFO, logger="cinetaste.guest.timing"):
        responses = await asyncio.gather(
            *(client.get(f"{api_prefix}/guest/cards", params={"limit": 12}) for _ in range(15))
        )
        later = await client.get(f"{api_prefix}/guest/cards", params={"limit": 12})

    assert all(r.status_code == 200 for r in responses)
    assert len(calls) == 1, "one request builds the deck; the rest wait for it"
    assert len({r.text for r in responses}) == 1, "everyone gets the same deck"
    assert later.text == responses[0].text
    assert len(calls) == 1, "and the cache serves it afterwards"

    outcomes = sorted(
        line["outcome"] for line in _timing_lines(caplog) if line["event"] == "guest_cards_timing"
    )
    assert outcomes == ["hit"] + ["miss"] + ["shared"] * 14


async def test_different_limits_are_different_flights(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    await seed_catalog(db_session)
    calls: list[dict] = []
    with _slow_cards(calls):
        await asyncio.gather(
            client.get(f"{api_prefix}/guest/cards", params={"limit": 8}),
            client.get(f"{api_prefix}/guest/cards", params={"limit": 8}),
            client.get(f"{api_prefix}/guest/cards", params={"limit": 12}),
        )
    assert sorted(c["limit"] for c in calls) == [8, 12]


async def test_paging_requests_are_not_coalesced(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    """`exclude` pages are different for every caller; sharing one would be wrong."""
    titles = await seed_catalog(db_session)
    calls: list[dict] = []

    def page(ids):  # noqa: ANN001, ANN202
        return client.get(
            f"{api_prefix}/guest/cards",
            params=[("limit", "8"), *(("exclude", str(t.id)) for t in ids)],
        )

    with _slow_cards(calls):
        first, second = await asyncio.gather(page(titles[:4]), page(titles[4:8]))
    assert len(calls) == 2
    shown = lambda r: {i["id"] for i in r.json()["items"]}  # noqa: E731
    assert not shown(first) & {str(t.id) for t in titles[:4]}
    assert not shown(second) & {str(t.id) for t in titles[4:8]}


async def test_a_failing_deck_build_fails_only_the_leader(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    await seed_catalog(db_session)
    calls: list[dict] = []
    with _slow_cards(calls, fail_first=True):
        responses = await asyncio.gather(
            *(client.get(f"{api_prefix}/guest/cards", params={"limit": 12}) for _ in range(6))
        )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] * 5 + [503], "followers do not inherit the leader's error"
    assert len(calls) == 6, "each follower then built the deck itself, as it did before single-flight"


# ------------------------------------------------------------ slate single-flight


def _tiny_cap_settings():  # noqa: ANN202
    return get_settings().model_copy(
        update={"guest_rank_max_concurrent": 1, "guest_rank_max_wait_seconds": 0.05}
    )


def _slow_slate(calls: list[int], *, fail_first: bool = False):  # noqa: ANN202
    from app.application.recommendation_service import RecommendationService
    from app.domain.exceptions import AppError

    real = RecommendationService.guest_slate

    async def wrapped(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        calls.append(len(calls) + 1)
        await asyncio.sleep(0.25)
        if fail_first and len(calls) == 1:
            raise AppError("ranking unavailable", status_code=503, code="rank_down")
        return await real(self, *args, **kwargs)

    return patch.object(RecommendationService, "guest_slate", wrapped)


async def test_identical_cold_requests_are_ranked_once_and_none_are_shed(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, caplog
) -> None:  # noqa: ANN001
    from app.api.deps import get_settings_dep
    from app.main import app

    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 8}
    calls: list[int] = []
    app.dependency_overrides[get_settings_dep] = _tiny_cap_settings
    try:
        with _slow_slate(calls), caplog.at_level(logging.INFO, logger="cinetaste.guest.timing"):
            responses = await asyncio.gather(*(client.post(url, json=body) for _ in range(12)))
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)

    # A cap of one slot and a 50 ms wait would refuse almost all of 12 simultaneous
    # rankings; identical requests are one ranking, so none of them is refused.
    assert [r.status_code for r in responses] == [200] * 12
    assert len(calls) == 1
    assert len({r.text for r in responses}) == 1

    slate = [line for line in _timing_lines(caplog) if line["event"] == "guest_slate_timing"]
    assert sorted(line["outcome"] for line in slate) == ["miss"] + ["shared"] * 11
    follower = next(line for line in slate if line["outcome"] == "shared")
    assert float(follower["flight_wait_ms"]) > 0
    assert "rank_ms" not in follower and "gate_wait_ms" not in follower and follower["db_statements"] == "0"


async def test_different_answers_are_still_capped_not_coalesced(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    """The contrast: single-flight must not turn the cap off for requests that differ."""
    from app.api.deps import get_settings_dep
    from app.main import app

    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    bodies = [
        {
            "reactions": [
                {"title_id": str(titles[i].id), "action": "rate_4"},
                {"title_id": str(titles[i + 1].id), "action": "rate_3"},
                {"title_id": str(titles[i + 2].id), "action": "rate_2"},
            ],
            "limit": 8,
        }
        for i in range(8)
    ]
    calls: list[int] = []
    app.dependency_overrides[get_settings_dep] = _tiny_cap_settings
    try:
        with _slow_slate(calls):
            responses = await asyncio.gather(*(client.post(url, json=b) for b in bodies))
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
    statuses = [r.status_code for r in responses]
    assert 503 in statuses, "distinct work beyond the cap is still refused"
    assert 200 in statuses
    for r in responses:
        if r.status_code == 503:
            assert r.json()["code"] == "guest_busy" and int(r.headers["Retry-After"]) >= 1


async def test_a_different_limit_is_a_different_flight(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    calls: list[int] = []
    with _slow_slate(calls):
        a, b = await asyncio.gather(
            client.post(url, json={"reactions": _reactions(titles), "limit": 6}),
            client.post(url, json={"reactions": _reactions(titles), "limit": 9}),
        )
    assert (a.status_code, b.status_code) == (200, 200)
    assert len(calls) == 2
    assert len(a.json()["items"]) <= 6 and len(b.json()["items"]) <= 9


async def test_a_failing_ranking_fails_only_the_leader(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 8}
    calls: list[int] = []
    with _slow_slate(calls, fail_first=True):
        responses = await asyncio.gather(*(client.post(url, json=body) for _ in range(6)))
    assert sorted(r.status_code for r in responses) == [200] * 5 + [503]
    assert len(calls) == 6, "followers then ranked for themselves, as before single-flight"
    # And what the survivors got is a real slate, not a half-shared failure.
    good = [r for r in responses if r.status_code == 200]
    assert len({r.text for r in good}) == 1 and good[0].json()["items"]


async def test_identical_invalid_requests_each_get_their_own_400(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    """A rejection is never shared or cached: every caller is validated on its own."""
    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles)[:2]}  # too few ratings
    responses = await asyncio.gather(*(client.post(url, json=body) for _ in range(5)))
    assert {r.status_code for r in responses} == {400}
    assert {r.json()["code"] for r in responses} == {"guest_insufficient_ratings"}
    assert (await client.post(url, json=body)).status_code == 400


async def test_the_shared_slate_matches_a_request_served_on_its_own(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from app.infrastructure.cache import reset_cache_for_tests

    titles = await seed_catalog(db_session)
    url = f"{api_prefix}/guest/recommendations"
    body = {"reactions": _reactions(titles), "limit": 10}
    calls: list[int] = []
    with _slow_slate(calls):
        burst = await asyncio.gather(*(client.post(url, json=body) for _ in range(5)))
    await reset_cache_for_tests()
    alone = await client.post(url, json=body)
    assert {r.text for r in burst} == {alone.text}


async def test_guest_slate_is_films_only_while_films_can_fill_it(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    """The guest page promises films. Live (Nov 2026) a guest slate held two anime
    TV series; series now come in only if there are not enough films."""
    titles = await seed_catalog(db_session)
    series = titles[12:20]
    for title in series:
        title.media_type = "tv"
        snapshot = dict(title.extra["feature_snapshot"])
        snapshot.pop("media:movie", None)
        snapshot["media:tv"] = 0.6
        title.extra = {**title.extra, "feature_snapshot": snapshot}
    await db_session.commit()

    reactions = [
        {"title_id": str(titles[0].id), "action": "rate_4"},
        {"title_id": str(titles[6].id), "action": "rate_3"},
        {"title_id": str(titles[1].id), "action": "rate_1"},
    ]
    res = await client.post(
        f"{api_prefix}/guest/recommendations", json={"reactions": reactions, "limit": 8}
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 8
    assert all(i["title"]["media_type"] == "movie" for i in items), [
        (i["title"]["name"], i["title"]["media_type"]) for i in items
    ]
