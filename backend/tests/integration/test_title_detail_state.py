"""The detail page knows what you already did with a title.

It used to offer "Save" on a film already in your watchlist and "Rate" on one
you had rated, because the endpoint returned the catalog entry only.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


async def _signed_in(client: AsyncClient, api: str) -> dict[str, str]:
    res = await client.post(
        f"{api}/auth/register",
        json={"email": f"detail-{uuid4().hex[:8]}@example.com", "password": "a-long-password-1"},
    )
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _status(client: AsyncClient, api: str, headers, title_id) -> tuple[str | None, str | None]:
    body = (await client.get(f"{api}/titles/{title_id}", headers=headers)).json()
    return body["my_state"], body["my_rating"]


async def _act(client: AsyncClient, api: str, headers, title_id, event: str) -> None:
    res = await client.post(
        f"{api}/titles/{title_id}/interactions", json={"event_type": event}, headers=headers
    )
    assert res.status_code == 204, res.text


async def test_detail_reports_the_viewers_state_and_exact_rating(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=6)
    headers = await _signed_in(client, api_prefix)
    title_id = titles[0].id

    assert await _status(client, api_prefix, headers, title_id) == (None, None)

    await _act(client, api_prefix, headers, title_id, "watchlist")
    assert await _status(client, api_prefix, headers, title_id) == ("watchlist", None)

    # "like" covers both Really liked and Loved; the page needs which one.
    await _act(client, api_prefix, headers, title_id, "rate_4")
    assert await _status(client, api_prefix, headers, title_id) == ("like", "rate_4")

    await _act(client, api_prefix, headers, title_id, "rate_1")
    assert await _status(client, api_prefix, headers, title_id) == ("dislike", "rate_1")

    await _act(client, api_prefix, headers, title_id, "clear")
    assert await _status(client, api_prefix, headers, title_id) == (None, None)

    await _act(client, api_prefix, headers, title_id, "not_interested")
    assert await _status(client, api_prefix, headers, title_id) == ("not_interested", None)


async def test_one_viewers_state_is_not_shown_to_another(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=6)
    alice = await _signed_in(client, api_prefix)
    bob = await _signed_in(client, api_prefix)

    await _act(client, api_prefix, alice, titles[0].id, "rate_4")
    assert await _status(client, api_prefix, alice, titles[0].id) == ("like", "rate_4")
    assert await _status(client, api_prefix, bob, titles[0].id) == (None, None)


# ---------------------------------------------------------------------------
# A weaker action never overwrites a rating (resolve_title_state)
# ---------------------------------------------------------------------------


async def _history_state(client: AsyncClient, api: str, headers, title_id) -> str | None:
    res = await client.get(f"{api}/me/history", params={"limit": 100}, headers=headers)
    assert res.status_code == 200, res.text
    for item in res.json()["items"]:
        if item["title"]["id"] == str(title_id):
            return item["state"]
    return None


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (["rate_4", "watchlist"], ("like", "rate_4")),
        (["rate_4", "watched"], ("like", "rate_4")),
        (["rate_3", "haven't_seen"], ("like", "rate_3")),
        (["watchlist", "rate_3"], ("like", "rate_3")),
        (["clear", "rate_1"], ("dislike", "rate_1")),
        (["rate_4", "clear", "rate_1"], ("dislike", "rate_1")),
        (["watchlist", "watched"], ("watched", None)),
        (["rate_4", "rate_2"], ("rated", "rate_2")),
    ],
)
async def test_detail_and_history_keep_the_effective_rating(
    client: AsyncClient,
    db_session: AsyncSession,
    api_prefix: str,
    sequence: list[str],
    expected: tuple[str, str | None],
) -> None:
    titles = await seed_catalog(db_session, count=6)
    headers = await _signed_in(client, api_prefix)
    title_id = titles[0].id

    for event in sequence:
        await _act(client, api_prefix, headers, title_id, event)

    # Reported on the detail page after a reload…
    assert await _status(client, api_prefix, headers, title_id) == expected
    # …and stored, so History and For You agree with it.
    assert await _history_state(client, api_prefix, headers, title_id) == expected[0]


async def test_ui_watchlist_path_cannot_erase_a_rating(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    """rate_4 → watchlist as the SPA sends it. The page no longer shows a
    watchlist toggle on a rated title, so no `clear` follows; the History
    "Clear" button is the explicit reset."""
    titles = await seed_catalog(db_session, count=6)
    headers = await _signed_in(client, api_prefix)
    title_id = titles[0].id

    await _act(client, api_prefix, headers, title_id, "rate_4")
    await _act(client, api_prefix, headers, title_id, "watchlist")
    assert await _status(client, api_prefix, headers, title_id) == ("like", "rate_4")

    watchlist = await client.get(f"{api_prefix}/watchlist", headers=headers)
    assert str(title_id) not in {t["id"] for t in watchlist.json()}

    await _act(client, api_prefix, headers, title_id, "clear")  # History "Clear"
    assert await _status(client, api_prefix, headers, title_id) == (None, None)


async def test_rated_title_does_not_return_to_for_you_via_havent_seen(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    from sqlalchemy import select

    from app.domain.taste_signals import FEED_EXCLUDE_STATES
    from app.infrastructure.db.models.interaction import UserTitleState

    titles = await seed_catalog(db_session, count=6)
    headers = await _signed_in(client, api_prefix)
    title_id = titles[0].id

    await _act(client, api_prefix, headers, title_id, "rate_3")
    await _act(client, api_prefix, headers, title_id, "haven't_seen")

    stored = await db_session.scalar(
        select(UserTitleState.state).where(UserTitleState.title_id == title_id)
    )
    assert stored == "like"
    assert stored in FEED_EXCLUDE_STATES
