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
