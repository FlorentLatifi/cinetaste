"""Endpoint coverage against real Postgres: every route, the main edge cases,
and the taste-learning behaviour end to end (event → profile → slate)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration

PASSWORD = "secure-pass-123"


async def _register(client: AsyncClient, api: str) -> dict[str, str]:
    email = f"u_{uuid4().hex[:10]}@example.com"
    res = await client.post(f"{api}/auth/register", json={"email": email, "password": PASSWORD})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}", "email": email}


def _auth(headers: dict[str, str]) -> dict[str, str]:
    return {"Authorization": headers["Authorization"]}


async def test_validation_errors_share_the_error_shape(client: AsyncClient, api_prefix: str) -> None:
    res = await client.post(f"{api_prefix}/auth/register", json={"email": "not-an-email"})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "validation_error"
    assert body["message"]
    assert {e["field"] for e in body["errors"]} >= {"email", "password"}


async def test_password_reset_flow_in_test_env(client: AsyncClient, api_prefix: str) -> None:
    user = await _register(client, api_prefix)
    # Access token minted before the reset — the one an attacker would be holding.
    assert (await client.get(f"{api_prefix}/me", headers=_auth(user))).status_code == 200

    # A JWT's `iat` is whole seconds, and the cut-off is compared at the same
    # resolution on purpose: a token minted in the *same* second as the change
    # has to survive, or a user who logs in right after resetting is bounced
    # straight back out. So the reset has to land in a later second than the
    # token above for this test to be testing anything. Do not delete the wait.
    await asyncio.sleep(1.1)

    forgot = await client.post(f"{api_prefix}/auth/forgot-password", json={"email": user["email"]})
    assert forgot.status_code == 200
    token = forgot.json()["dev_reset_token"]
    assert token

    reset = await client.post(
        f"{api_prefix}/auth/reset-password", json={"token": token, "new_password": "brand-new-pass-1"}
    )
    assert reset.status_code == 204

    # Revoking refresh tokens is not enough: an access token is stateless and
    # would otherwise keep working for the rest of its TTL after the victim
    # thought they had locked the attacker out.
    stale = await client.get(f"{api_prefix}/me", headers=_auth(user))
    assert stale.status_code == 401
    reused = await client.post(
        f"{api_prefix}/auth/reset-password", json={"token": token, "new_password": "another-pass-22"}
    )
    assert reused.status_code == 400

    old = await client.post(
        f"{api_prefix}/auth/login", json={"email": user["email"], "password": PASSWORD}
    )
    assert old.status_code == 401
    new = await client.post(
        f"{api_prefix}/auth/login", json={"email": user["email"], "password": "brand-new-pass-1"}
    )
    assert new.status_code == 200


async def test_concurrent_refresh_with_same_cookie_keeps_session(
    client: AsyncClient, api_prefix: str
) -> None:
    """Two tabs refreshing at once used to trip reuse detection and log out."""
    await _register(client, api_prefix)
    cookie = client.cookies.get("ct_refresh")
    first, second = await asyncio.gather(
        client.post(f"{api_prefix}/auth/refresh", cookies={"ct_refresh": cookie}),
        client.post(f"{api_prefix}/auth/refresh", cookies={"ct_refresh": cookie}),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text


async def test_logout_revokes_refresh(client: AsyncClient, api_prefix: str) -> None:
    await _register(client, api_prefix)
    cookie = client.cookies.get("ct_refresh")
    out = await client.post(f"{api_prefix}/auth/logout")
    assert out.status_code == 204
    again = await client.post(f"{api_prefix}/auth/refresh", cookies={"ct_refresh": cookie})
    assert again.status_code == 401


async def test_catalog_browsing_endpoints(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=18)
    headers = _auth(await _register(client, api_prefix))
    target = titles[0]

    detail = await client.get(f"{api_prefix}/titles/{target.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == target.name
    assert detail.json()["genres"]

    missing = await client.get(f"{api_prefix}/titles/{uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    similar = await client.get(f"{api_prefix}/titles/{target.id}/similar?limit=5", headers=headers)
    assert similar.status_code == 200
    ids = [t["id"] for t in similar.json()]
    assert len(ids) == 5 and str(target.id) not in ids

    exact = await client.get(f"{api_prefix}/titles/search?q=Integration Film 03", headers=headers)
    assert exact.status_code == 200
    assert exact.json()[0]["name"] == "Integration Film 03"

    typo = await client.get(f"{api_prefix}/titles/search?q=Integraton Flim", headers=headers)
    assert typo.status_code == 200
    assert typo.json(), "pg_trgm should match a misspelled query"

    wildcard = await client.get(f"{api_prefix}/titles/search?q=%25%25", headers=headers)
    assert wildcard.status_code == 200
    assert wildcard.json() == []  # "%%" is a literal, not match-everything

    status = await client.get(f"{api_prefix}/catalog/status", headers=headers)
    assert status.json()["with_embeddings"] == 18


async def test_interactions_drive_profile_library_and_slate(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=24)
    headers = _auth(await _register(client, api_prefix))
    liked, saved, flipped = titles[0], titles[1], titles[2]

    async def act(title, event: str) -> None:
        res = await client.post(
            f"{api_prefix}/titles/{title.id}/interactions",
            headers=headers,
            json={"event_type": event},
        )
        assert res.status_code == 204, res.text

    await act(liked, "rate_4")
    await act(saved, "watchlist")
    await act(flipped, "like")
    await act(flipped, "dislike")  # latest opinion must win

    bad = await client.post(
        f"{api_prefix}/titles/{liked.id}/interactions", headers=headers, json={"event_type": "love"}
    )
    assert bad.status_code == 422

    taste = (await client.get(f"{api_prefix}/me/taste", headers=headers)).json()
    assert taste["ready"] and taste["has_vector"]
    assert taste["anchor_count"] == 1  # only the rate_4 title is a strong anchor

    watchlist = (await client.get(f"{api_prefix}/watchlist", headers=headers)).json()
    assert [t["id"] for t in watchlist] == [str(saved.id)]

    page1 = await client.get(f"{api_prefix}/me/history?limit=2", headers=headers)
    body1 = page1.json()
    assert len(body1["items"]) == 2 and body1["has_more"]
    page2 = await client.get(
        f"{api_prefix}/me/history?limit=2&cursor={body1['next_cursor']}", headers=headers
    )
    assert len(page2.json()["items"]) == 1
    states = {i["title"]["id"]: i["state"] for i in body1["items"] + page2.json()["items"]}
    assert states[str(flipped.id)] == "dislike"

    bad_filter = await client.get(f"{api_prefix}/me/history?state=bogus", headers=headers)
    assert bad_filter.status_code == 400

    slate = (await client.get(f"{api_prefix}/recommendations/for-you?limit=10", headers=headers)).json()
    shown = {row["title"]["id"] for row in slate["items"]}
    assert shown.isdisjoint({str(liked.id), str(saved.id), str(flipped.id)})
    assert slate["slate_id"]

    cached = (await client.get(f"{api_prefix}/recommendations/for-you?limit=10", headers=headers)).json()
    assert cached["slate_id"] == slate["slate_id"]  # served from cache

    # Undo: clearing the dislike lets the title come back and changes the profile.
    await act(flipped, "clear")
    fresh = (await client.get(f"{api_prefix}/recommendations/for-you?limit=24", headers=headers)).json()
    assert fresh["slate_id"] != slate["slate_id"]
    assert str(flipped.id) in {row["title"]["id"] for row in fresh["items"]}


async def test_taste_export_import_roundtrip(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    titles = await seed_catalog(db_session, count=12)
    headers = _auth(await _register(client, api_prefix))
    await client.post(
        f"{api_prefix}/titles/{titles[0].id}/interactions",
        headers=headers,
        json={"event_type": "rate_4"},
    )
    export = (await client.get(f"{api_prefix}/me/taste/export", headers=headers)).json()
    assert export["likes"]

    other = _auth(await _register(client, api_prefix))
    imported = await client.post(
        f"{api_prefix}/me/taste/import",
        headers=other,
        json={"schema": export["schema"], "likes": export["likes"], "dislikes": export["dislikes"]},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["summary"]["has_import_overlay"]

    too_long = await client.post(
        f"{api_prefix}/me/taste/import",
        headers=other,
        json={"schema": export["schema"], "likes": [{"key": "genre:" + "x" * 500, "weight": 1.0}]},
    )
    assert too_long.status_code == 422

    cleared = await client.delete(f"{api_prefix}/me/taste/import", headers=other)
    assert cleared.status_code == 200
    assert not cleared.json()["has_import_overlay"]


async def test_onboarding_rejects_unknown_titles(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    await seed_catalog(db_session, count=12)
    headers = _auth(await _register(client, api_prefix))
    reactions = [{"title_id": str(uuid4()), "action": "rate_4"} for _ in range(6)]
    res = await client.post(
        f"{api_prefix}/onboarding/complete", headers=headers, json={"reactions": reactions}
    )
    assert res.status_code == 400
    assert res.json()["code"] == "unknown_titles"


async def test_delete_account(client: AsyncClient, api_prefix: str) -> None:
    user = await _register(client, api_prefix)
    headers = _auth(user)
    wrong = await client.request(
        "DELETE", f"{api_prefix}/me", headers=headers, json={"password": "nope", "confirm": "DELETE"}
    )
    assert wrong.status_code == 401
    ok = await client.request(
        "DELETE", f"{api_prefix}/me", headers=headers, json={"password": PASSWORD, "confirm": "DELETE"}
    )
    assert ok.status_code == 204
    gone = await client.get(f"{api_prefix}/me", headers=headers)
    assert gone.status_code == 401
