"""Guest mode: the onboarding deck and a ranked slate with no account.

The promise is "nothing about you is stored", so beyond the happy path these
check the tables a signed-in flow would write to stay empty.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.interaction import (
    InteractionEvent,
    RecommendationImpression,
    UserTitleState,
)
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.models.user import User
from tests.integration.conftest import seed_catalog


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
