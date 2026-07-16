"""Unit tests for password reset + account delete (mocked session)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.auth_service import AuthService
from app.core.config import Settings
from app.core.security import hash_password, hash_token, verify_password
from app.domain.exceptions import AppError, UnauthorizedError
from app.infrastructure.db.models.user import PasswordResetToken, User


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        app_env="local",
        public_app_url="http://localhost:5173",
        password_reset_ttl_minutes=60,
    )
    base.update(kwargs)
    return Settings(**base)


def _user(password: str = "old-password-99") -> User:
    return User(
        id=uuid4(),
        email="a@example.com",
        password_hash=hash_password(password),
        display_name="A",
    )


@pytest.mark.asyncio
async def test_request_password_reset_unknown_email() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    auth = AuthService(session, _settings())
    token = await auth.request_password_reset(email="nobody@example.com")
    assert token is None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_request_password_reset_known_email_dev_returns_token() -> None:
    session = AsyncMock()
    user = _user()
    session.scalar = AsyncMock(return_value=user)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    auth = AuthService(session, _settings(app_env="local"))
    raw = await auth.request_password_reset(email=user.email)
    assert raw is not None
    assert len(raw) >= 20
    session.add.assert_called()
    row = session.add.call_args[0][0]
    assert isinstance(row, PasswordResetToken)
    assert row.token_hash == hash_token(raw)


@pytest.mark.asyncio
async def test_request_password_reset_production_hides_token() -> None:
    session = AsyncMock()
    user = _user()
    session.scalar = AsyncMock(return_value=user)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    auth = AuthService(
        session,
        _settings(
            app_env="production",
            jwt_secret="x" * 48,
            cors_origins="https://cinetaste.vercel.app",
        ),
    )
    raw = await auth.request_password_reset(email=user.email)
    assert raw is None


@pytest.mark.asyncio
async def test_reset_password_success() -> None:
    session = AsyncMock()
    user = _user("old-password-99")
    raw = "reset-token-value-123456"
    stored = PasswordResetToken(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        used_at=None,
    )

    async def scalar_side_effect(stmt):
        # first call: load token; second could be user get
        return stored

    session.scalar = AsyncMock(side_effect=[stored])
    session.get = AsyncMock(return_value=user)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    auth = AuthService(session, _settings())
    await auth.reset_password(token=raw, new_password="new-password-99")
    assert verify_password("new-password-99", user.password_hash)
    assert stored.used_at is not None


@pytest.mark.asyncio
async def test_reset_password_expired() -> None:
    session = AsyncMock()
    raw = "expired-token-abcdefgh"
    stored = PasswordResetToken(
        id=uuid4(),
        user_id=uuid4(),
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        used_at=None,
    )
    session.scalar = AsyncMock(return_value=stored)
    auth = AuthService(session, _settings())
    with pytest.raises(AppError) as exc:
        await auth.reset_password(token=raw, new_password="new-password-99")
    assert exc.value.code == "invalid_reset_token"


@pytest.mark.asyncio
async def test_delete_account_wrong_password() -> None:
    session = AsyncMock()
    user = _user("correct-horse")
    auth = AuthService(session, _settings())
    with pytest.raises(UnauthorizedError):
        await auth.delete_account(user=user, password="wrong")


@pytest.mark.asyncio
async def test_delete_account_success() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    user = _user("correct-horse")
    auth = AuthService(session, _settings())
    await auth.delete_account(user=user, password="correct-horse")
    assert session.delete.await_count == 1 or session.delete.call_count == 1
