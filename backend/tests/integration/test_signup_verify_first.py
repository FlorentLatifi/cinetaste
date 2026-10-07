"""Verify-first sign-up: with verification required, no account exists until the
mailed link is opened *with* the password chosen at sign-up.

The threat these pin down: typing someone else's address must not create an
account under it, take the address, or let the impostor in if the real owner
clicks the link.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_settings_dep
from app.core.config import get_settings
from app.infrastructure.db.models.user import PendingRegistration, User
from app.main import app

pytestmark = pytest.mark.integration

PASSWORD = "a-long-enough-password-1"


@pytest.fixture
def verification_required() -> Iterator[None]:
    # APP_ENV=test without SMTP: the token comes back in the response, as it
    # would arrive by email in production.
    settings = get_settings().model_copy(update={"require_email_verification": True})
    app.dependency_overrides[get_settings_dep] = lambda: settings
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _register(client: AsyncClient, api: str, email: str, password: str = PASSWORD):
    return await client.post(
        f"{api}/auth/register", json={"email": email, "password": password}
    )


async def test_no_account_exists_until_the_link_is_used(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, verification_required: None
) -> None:
    res = await _register(client, api_prefix, "Someone@Example.com")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "confirmation_sent"
    assert body["email"] == "someone@example.com"
    assert "access_token" not in body
    assert client.cookies.get("ct_refresh") is None
    token = body["dev_confirmation_token"]
    assert token

    assert await _count(db_session, User) == 0
    assert await _count(db_session, PendingRegistration) == 1

    # The link alone is not enough.
    wrong = await client.post(
        f"{api_prefix}/auth/confirm-registration",
        json={"token": token, "password": "not-the-password-99"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"
    assert await _count(db_session, User) == 0

    ok = await client.post(
        f"{api_prefix}/auth/confirm-registration", json={"token": token, "password": PASSWORD}
    )
    assert ok.status_code == 201, ok.text
    session = ok.json()
    assert session["access_token"]
    assert session["user"]["email"] == "someone@example.com"
    assert session["user"]["email_verified_at"] is not None
    assert session["email_verification_required"] is False
    assert client.cookies.get("ct_refresh")

    db_session.expire_all()
    assert await _count(db_session, User) == 1
    assert await _count(db_session, PendingRegistration) == 0

    # A verified account passes the gate on product routes.
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    watchlist = await client.get(f"{api_prefix}/watchlist", headers=headers)
    assert watchlist.status_code == 200

    # One use only.
    again = await client.post(
        f"{api_prefix}/auth/confirm-registration", json={"token": token, "password": PASSWORD}
    )
    assert again.status_code == 400
    assert again.json()["code"] == "invalid_confirmation_token"


async def test_signing_up_again_replaces_the_earlier_link(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, verification_required: None
) -> None:
    first = (await _register(client, api_prefix, "twice@example.com")).json()
    second = (await _register(client, api_prefix, "twice@example.com", "another-password-22")).json()
    assert await _count(db_session, PendingRegistration) == 1

    old = await client.post(
        f"{api_prefix}/auth/confirm-registration",
        json={"token": first["dev_confirmation_token"], "password": PASSWORD},
    )
    assert old.status_code == 400

    new = await client.post(
        f"{api_prefix}/auth/confirm-registration",
        json={"token": second["dev_confirmation_token"], "password": "another-password-22"},
    )
    assert new.status_code == 201, new.text


async def test_an_existing_address_gets_the_same_answer_and_nothing_changes(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    # Registered before verification was switched on.
    created = await _register(client, api_prefix, "taken@example.com")
    assert created.status_code == 201

    settings = get_settings().model_copy(update={"require_email_verification": True})
    app.dependency_overrides[get_settings_dep] = lambda: settings
    try:
        res = await _register(client, api_prefix, "taken@example.com", "attacker-password-1")
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)

    # Same status and shape as a new address: no way to test who is registered.
    assert res.status_code == 202
    assert res.json()["status"] == "confirmation_sent"
    assert res.json()["dev_confirmation_token"] is None
    assert await _count(db_session, User) == 1
    assert await _count(db_session, PendingRegistration) == 0


async def test_an_expired_link_creates_nothing(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str, verification_required: None
) -> None:
    token = (await _register(client, api_prefix, "late@example.com")).json()[
        "dev_confirmation_token"
    ]
    await db_session.execute(
        update(PendingRegistration).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()

    res = await client.post(
        f"{api_prefix}/auth/confirm-registration", json={"token": token, "password": PASSWORD}
    )
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_confirmation_token"
    assert await _count(db_session, User) == 0

    # The next sign-up sweeps the stale row away.
    await _register(client, api_prefix, "other@example.com")
    pending = (await db_session.scalars(select(PendingRegistration.email))).all()
    assert pending == ["other@example.com"]


async def test_without_the_setting_sign_up_is_unchanged(
    client: AsyncClient, db_session: AsyncSession, api_prefix: str
) -> None:
    res = await _register(client, api_prefix, "plain@example.com")
    assert res.status_code == 201
    assert res.json()["access_token"]
    assert await _count(db_session, PendingRegistration) == 0
