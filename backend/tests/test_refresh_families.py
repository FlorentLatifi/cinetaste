"""Refresh token family reuse detection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.auth_service import AuthService
from app.core.config import Settings
from app.core.security import hash_password, hash_token
from app.domain.exceptions import UnauthorizedError
from app.infrastructure.db.models.user import RefreshToken, User


def _settings() -> Settings:
    return Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
    )


@pytest.mark.asyncio
async def test_reuse_of_revoked_refresh_kills_family() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    user = User(
        id=uuid4(),
        email="x@example.com",
        password_hash=hash_password("password123"),
    )
    family = uuid4()
    old_raw = "old-refresh-token-value-aaaa"
    stored = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        family_id=family,
        token_hash=hash_token(old_raw),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        revoked_at=datetime.now(UTC),  # already rotated away
    )

    session.scalar = AsyncMock(return_value=stored)
    auth = AuthService(session, _settings())

    with pytest.raises(UnauthorizedError) as exc:
        await auth.refresh(refresh_token=old_raw)
    assert exc.value.code == "refresh_reuse"
    session.execute.assert_awaited()  # family revoke update


@pytest.mark.asyncio
async def test_just_rotated_token_gets_sibling_instead_of_family_kill() -> None:
    """Two tabs refreshing with the same cookie must not log the user out."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    user = User(id=uuid4(), email="x@example.com", password_hash="unused")
    session.get = AsyncMock(return_value=user)
    family = uuid4()
    raw = "rotated-moments-ago-token-value"
    stored = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        family_id=family,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        revoked_at=datetime.now(UTC) - timedelta(seconds=2),
        replaced_by_id=uuid4(),
    )
    session.scalar = AsyncMock(return_value=stored)

    returned_user, access, new_refresh = await AuthService(session, _settings()).refresh(
        refresh_token=raw
    )
    assert returned_user is user
    assert access and new_refresh and new_refresh != raw
    session.execute.assert_not_awaited()  # no family revoke
    sibling = session.add.call_args[0][0]
    assert sibling.family_id == family


@pytest.mark.asyncio
async def test_rotation_records_successor() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    user = User(id=uuid4(), email="x@example.com", password_hash="unused")
    session.get = AsyncMock(return_value=user)
    raw = "live-refresh-token-value-bbbb"
    stored = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        family_id=uuid4(),
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        revoked_at=None,
    )
    session.scalar = AsyncMock(return_value=stored)
    await AuthService(session, _settings()).refresh(refresh_token=raw)
    assert stored.revoked_at is not None
    assert stored.replaced_by_id == session.add.call_args[0][0].id
