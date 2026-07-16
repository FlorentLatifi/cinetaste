"""End-to-end API flow against real Postgres + Redis.

Happy path: register → seed catalog → onboarding cards → complete → for-you.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_health_and_ready(client: AsyncClient, api_prefix: str) -> None:
    health = await client.get(f"{api_prefix}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = await client.get(f"{api_prefix}/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"


@pytest.mark.asyncio
async def test_register_login_refresh_me(
    client: AsyncClient,
    api_prefix: str,
) -> None:
    email = f"user_{uuid4().hex[:10]}@example.com"
    password = "secure-pass-123"

    reg = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": email, "password": password, "display_name": "Tester"},
    )
    assert reg.status_code == 201, reg.text
    tokens = reg.json()
    assert tokens["access_token"]
    assert "refresh_token" not in tokens  # httpOnly cookie only
    assert tokens["user"]["email"] == email
    assert tokens["user"]["onboarding_completed_at"] is None
    assert "ct_refresh" in client.cookies

    me = await client.get(
        f"{api_prefix}/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == email

    login = await client.post(
        f"{api_prefix}/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    old_cookie = client.cookies.get("ct_refresh")
    assert old_cookie

    # Cookie-only refresh (no body token)
    refreshed = await client.post(f"{api_prefix}/auth/refresh", json={})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    new_cookie = client.cookies.get("ct_refresh")
    assert new_cookie
    assert new_cookie != old_cookie  # rotation

    # Reusing the previous raw cookie value must fail
    client.cookies.set("ct_refresh", old_cookie, path=f"{api_prefix}/auth")
    reused = await client.post(f"{api_prefix}/auth/refresh", json={})
    assert reused.status_code == 401


@pytest.mark.asyncio
async def test_onboarding_gates_and_full_recommendation_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    api_prefix: str,
) -> None:
    await seed_catalog(db_session, count=24)

    email = f"flow_{uuid4().hex[:10]}@example.com"
    reg = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": email, "password": "secure-pass-123", "display_name": "Flow"},
    )
    assert reg.status_code == 201
    access = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    cards = await client.get(f"{api_prefix}/onboarding/cards", headers=headers)
    assert cards.status_code == 200, cards.text
    items = cards.json()["items"]
    assert len(items) >= 8

    # Too few ratings → 400, onboarding not completed
    bad = await client.post(
        f"{api_prefix}/onboarding/complete",
        headers=headers,
        json={
            "reactions": [
                {"title_id": items[0]["id"], "action": "rate_3"},
                {"title_id": items[1]["id"], "action": "haven't_seen"},
            ]
        },
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "onboarding_insufficient_ratings"

    me_mid = await client.get(f"{api_prefix}/me", headers=headers)
    assert me_mid.json()["onboarding_completed_at"] is None

    # Build valid reactions: 6 ratings (2+ positive) + skips
    reactions = []
    for i, title in enumerate(items[:12]):
        if i < 2:
            action = "rate_4"
        elif i < 4:
            action = "rate_3"
        elif i < 6:
            action = "rate_1"
        elif i < 8:
            action = "haven't_seen"
        else:
            action = "not_interested"
        reactions.append({"title_id": title["id"], "action": action})

    done = await client.post(
        f"{api_prefix}/onboarding/complete",
        headers=headers,
        json={"reactions": reactions},
    )
    assert done.status_code == 200, done.text
    assert done.json()["onboarding_completed_at"] is not None

    for_you = await client.get(
        f"{api_prefix}/recommendations/for-you?limit=10",
        headers=headers,
    )
    assert for_you.status_code == 200, for_you.text
    slate = for_you.json()["items"]
    assert len(slate) >= 1
    for row in slate:
        assert "title" in row
        assert "score" in row
        assert isinstance(row.get("reasons"), list)
        assert row["reasons"], "each rec should include at least one reason"
        assert row["reasons"][0]["message"]

    # Rated / not_interested titles should not reappear on the slate
    excluded_ids = {
        r["title_id"]
        for r in reactions
        if r["action"] in {"rate_1", "rate_3", "rate_4", "not_interested"}
    }
    slate_ids = {row["title"]["id"] for row in slate}
    assert slate_ids.isdisjoint(excluded_ids)

    # Cache path: second call still works
    again = await client.get(
        f"{api_prefix}/recommendations/for-you?limit=10",
        headers=headers,
    )
    assert again.status_code == 200
    assert len(again.json()["items"]) >= 1


@pytest.mark.asyncio
async def test_for_you_requires_auth(client: AsyncClient, api_prefix: str) -> None:
    res = await client.get(f"{api_prefix}/recommendations/for-you")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_catalog_status_after_seed(
    client: AsyncClient,
    db_session: AsyncSession,
    api_prefix: str,
) -> None:
    await seed_catalog(db_session, count=12)
    email = f"cat_{uuid4().hex[:8]}@example.com"
    reg = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": email, "password": "secure-pass-123"},
    )
    access = reg.json()["access_token"]
    status = await client.get(
        f"{api_prefix}/catalog/status",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert status.status_code == 200
    body = status.json()
    assert body["title_count"] >= 12
    assert body["with_embeddings"] >= 12
    assert body["ready_for_onboarding"] is True
