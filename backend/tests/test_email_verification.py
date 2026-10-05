"""Email ownership: issuing, consuming and enforcing verification.

Registration used to accept any address without proof, so an account could be
opened under someone else's email — and that person would then receive the
password-reset mail for an account they never created.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.deps import get_verified_user
from app.api.routes.auth import _token_response
from app.application.auth_service import AuthService
from app.core.config import Settings
from app.core.security import hash_password, hash_token
from app.domain.exceptions import AppError, ConflictError, ForbiddenError
from app.infrastructure.cache import MemoryStore
from app.infrastructure.db.models.user import EmailVerificationToken, PasswordResetToken, User


@pytest.fixture(autouse=True)
def isolated_throttle(monkeypatch) -> None:
    """Issuing a link is throttled per account; keep tests independent."""
    fresh = MemoryStore()
    monkeypatch.setattr("app.core.throttle.get_rate_limit_store", lambda: fresh)


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        app_env="local",
        app_debug=False,
        public_app_url="http://localhost:5173",
    )
    base.update(kwargs)
    return Settings(**base)


def _user(*, verified: bool = False) -> User:
    return User(
        id=uuid4(),
        email="a@example.com",
        password_hash=hash_password("old-password-99"),
        display_name="A",
        email_verified_at=datetime.now(UTC) if verified else None,
    )


def _session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


# ------------------------------------------------------------------- issuing


@pytest.mark.asyncio
async def test_dev_without_smtp_returns_the_raw_token() -> None:
    """Same escape hatch as password reset, so the flow is exercisable locally."""
    session = _session()
    user = _user()
    auth = AuthService(session, _settings(app_env="local"))

    raw = await auth.request_email_verification(user)

    assert raw is not None and len(raw) >= 20
    row = session.add.call_args[0][0]
    assert isinstance(row, EmailVerificationToken)
    # Only the hash is stored: a leaked row must not be a working link.
    assert row.token_hash == hash_token(raw)
    assert raw not in row.token_hash


@pytest.mark.asyncio
async def test_staging_without_smtp_refuses_instead_of_handing_out_a_token() -> None:
    """Returning the token outside dev would let anyone verify any address."""
    session = _session()
    auth = AuthService(session, _settings(app_env="staging"))

    with pytest.raises(AppError) as exc:
        await auth.request_email_verification(_user())
    assert exc.value.status_code == 503
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_an_already_verified_address_is_not_re_issued() -> None:
    session = _session()
    auth = AuthService(session, _settings())

    with pytest.raises(ConflictError):
        await auth.request_email_verification(_user(verified=True))
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_issuing_retires_the_previous_unused_token() -> None:
    """A link the user replaced should stop working."""
    session = _session()
    auth = AuthService(session, _settings())

    await auth.request_email_verification(_user())

    assert session.execute.await_count == 1  # the UPDATE ... SET used_at


@pytest.mark.asyncio
async def test_issuing_is_throttled_per_account() -> None:
    """Otherwise the endpoint is a mail cannon pointed at one address."""
    settings = _settings(rate_limit_account_failures=3)
    user = _user()
    for _ in range(3):
        await AuthService(_session(), settings).request_email_verification(user)

    with pytest.raises(AppError) as exc:
        await AuthService(_session(), settings).request_email_verification(user)
    assert exc.value.status_code == 429


# ------------------------------------------------------------------ consuming


def _token_row(user: User, raw: str, **overrides) -> EmailVerificationToken:
    data = dict(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(hours=48),
        used_at=None,
    )
    data.update(overrides)
    return EmailVerificationToken(**data)


@pytest.mark.asyncio
async def test_verifying_stamps_the_user_and_burns_the_token() -> None:
    user = _user()
    raw = "a-verification-token-value"
    row = _token_row(user, raw)
    session = _session()
    session.scalar = AsyncMock(return_value=row)
    session.get = AsyncMock(return_value=user)

    verified = await AuthService(session, _settings()).verify_email(token=raw)

    assert verified.email_verified_at is not None
    assert row.used_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row_kwargs",
    [
        {"used_at": datetime.now(UTC)},
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)},
    ],
    ids=["already used", "expired"],
)
async def test_used_and_expired_tokens_fail_identically(row_kwargs) -> None:
    """A caller guessing tokens must not learn which kind of miss it was."""
    user = _user()
    raw = "a-verification-token-value"
    session = _session()
    session.scalar = AsyncMock(return_value=_token_row(user, raw, **row_kwargs))
    session.get = AsyncMock(return_value=user)

    with pytest.raises(AppError) as exc:
        await AuthService(session, _settings()).verify_email(token=raw)
    assert exc.value.status_code == 400
    assert exc.value.code == "invalid_verification_token"
    assert user.email_verified_at is None


@pytest.mark.asyncio
async def test_unknown_token_fails_the_same_way() -> None:
    session = _session()
    session.scalar = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc:
        await AuthService(session, _settings()).verify_email(token="never-existed-token")
    assert exc.value.code == "invalid_verification_token"


@pytest.mark.asyncio
async def test_clicking_the_link_twice_is_not_an_error_for_the_user() -> None:
    """People do this. The second click must not read as a failure."""
    user = _user()
    raw = "a-verification-token-value"
    session = _session()
    session.scalar = AsyncMock(return_value=_token_row(user, raw))
    session.get = AsyncMock(return_value=user)
    auth = AuthService(session, _settings())

    first = await auth.verify_email(token=raw)
    stamped = first.email_verified_at

    # A second, still-valid token for an account that is already verified.
    session.scalar = AsyncMock(return_value=_token_row(user, raw))
    second = await auth.verify_email(token=raw)
    assert second.email_verified_at == stamped  # not moved, not rejected


# ----------------------------------------------------------------- enforcing


@pytest.mark.asyncio
async def test_unverified_user_is_blocked_only_when_the_deployment_asks() -> None:
    user = _user()

    # Default: nothing changes for anyone.
    assert await get_verified_user(user, _settings()) is user

    strict = _settings(
        require_email_verification=True,
        smtp_host="smtp.example.com",
        smtp_from="noreply@example.com",
    )
    with pytest.raises(ForbiddenError) as exc:
        await get_verified_user(user, strict)
    assert exc.value.status_code == 403
    assert exc.value.code == "email_not_verified"

    assert await get_verified_user(_user(verified=True), strict) is not None


def test_requiring_verification_without_smtp_is_refused_in_production() -> None:
    """Otherwise the first deploy locks every account out, with no way to fix it."""
    base = dict(
        app_env="production",
        app_debug=False,
        jwt_secret="a-strong-unique-production-secret-value-1234",
        database_url="postgresql+asyncpg://user:pw@db.example.com/ct",
        cors_origins="https://cinetaste.vercel.app",
        public_app_url="https://cinetaste.vercel.app",
    )
    with pytest.raises(ValidationError):
        Settings(**base, require_email_verification=True)

    Settings(
        **base,
        require_email_verification=True,
        smtp_host="smtp.example.com",
        smtp_from="noreply@cinetaste.app",
    )


# ------------------------------------------------ proving ownership by reset


@pytest.mark.asyncio
async def test_a_password_reset_proves_the_mailbox() -> None:
    """The reset link was opened from the inbox; that is what verification proves.

    It also resolves squatting: the owner of an address someone else
    registered resets the password and ends up verified, and the reset revokes
    the squatter's sessions.
    """
    user = _user()
    raw = "a-reset-token-value-1234"
    row = PasswordResetToken(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    session = _session()
    session.scalar = AsyncMock(return_value=row)
    session.get = AsyncMock(return_value=user)

    await AuthService(session, _settings()).reset_password(token=raw, new_password="new-password-99")

    assert user.email_verified_at is not None


@pytest.mark.asyncio
async def test_a_reset_keeps_the_original_verification_time() -> None:
    user = _user(verified=True)
    first = user.email_verified_at
    raw = "another-reset-token-5678"
    row = PasswordResetToken(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    session = _session()
    session.scalar = AsyncMock(return_value=row)
    session.get = AsyncMock(return_value=user)

    await AuthService(session, _settings()).reset_password(token=raw, new_password="new-password-99")

    assert user.email_verified_at == first


def test_the_session_says_up_front_whether_verification_is_owed() -> None:
    """The SPA shows "check your inbox" right after sign-up, not after a 403."""
    strict = _settings(
        require_email_verification=True,
        smtp_host="smtp.example.com",
        smtp_from="noreply@example.com",
    )
    user = _user()
    user.created_at = datetime.now(UTC)

    assert _token_response(user, "access", strict).email_verification_required is True
    assert _token_response(user, "access", _settings()).email_verification_required is False

    user.email_verified_at = datetime.now(UTC)
    assert _token_response(user, "access", strict).email_verification_required is False
