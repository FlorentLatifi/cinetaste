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
