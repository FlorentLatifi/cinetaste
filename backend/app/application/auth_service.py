from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.domain.exceptions import AppError, ConflictError, UnauthorizedError
from app.infrastructure.db.models.user import PasswordResetToken, RefreshToken, User

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def register(
        self, *, email: str, password: str, display_name: str | None = None
    ) -> tuple[User, str, str]:
        normalized = email.strip().lower()
        existing = await self._session.scalar(select(User).where(User.email == normalized))
        if existing:
            raise ConflictError("An account with this email already exists", code="email_taken")

        user = User(
            email=normalized,
            password_hash=hash_password(password),
            display_name=display_name.strip() if display_name else None,
        )
        self._session.add(user)
        await self._session.flush()

        access, refresh = await self._issue_tokens(user)
        return user, access, refresh

    async def login(self, *, email: str, password: str) -> tuple[User, str, str]:
        normalized = email.strip().lower()
        user = await self._session.scalar(select(User).where(User.email == normalized))
        if user is None or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

        access, refresh = await self._issue_tokens(user)
        return user, access, refresh

    async def refresh(self, *, refresh_token: str) -> tuple[User, str, str]:
        token_hash = hash_token(refresh_token)
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if stored is None or stored.revoked_at is not None:
            raise UnauthorizedError("Invalid refresh token", code="invalid_refresh")

        now = datetime.now(UTC)
        expires = stored.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            raise UnauthorizedError("Refresh token expired", code="refresh_expired")

        user = await self._session.get(User, stored.user_id)
        if user is None:
            raise UnauthorizedError("User not found", code="invalid_refresh")

        # Rotate refresh token
        stored.revoked_at = now
        access, new_refresh = await self._issue_tokens(user)
        return user, access, new_refresh

    async def logout(self, *, refresh_token: str) -> None:
        token_hash = hash_token(refresh_token)
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)

    async def request_password_reset(self, *, email: str) -> str | None:
        """Create a one-time reset token if the account exists.

        Always safe for callers to return a generic success to the client.
        Returns the raw token only for non-production logging / dev UX — never
        required by the HTTP API response.
        """
        normalized = email.strip().lower()
        user = await self._session.scalar(select(User).where(User.email == normalized))
        if user is None:
            return None

        # Invalidate prior unused tokens for this user
        await self._session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )

        raw = secrets.token_urlsafe(32)
        row = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=self._settings.password_reset_ttl_minutes),
        )
        self._session.add(row)
        await self._session.flush()

        # No outbound email provider in MVP — log a reset path for ops/dev.
        link = f"{self._settings.public_app_url.rstrip('/')}/reset-password?token={raw}"
        logger.info(
            "password_reset_issued user_id=%s email=%s expires_minutes=%s link=%s",
            user.id,
            user.email,
            self._settings.password_reset_ttl_minutes,
            link if not self._settings.is_production else "[redacted]",
        )
        if not self._settings.is_production:
            return raw
        return None

    async def reset_password(self, *, token: str, new_password: str) -> None:
        token_hash = hash_token(token.strip())
        stored = await self._session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        if stored is None or stored.used_at is not None:
            raise AppError("Invalid or expired reset link", status_code=400, code="invalid_reset_token")

        now = datetime.now(UTC)
        expires = stored.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            raise AppError("Invalid or expired reset link", status_code=400, code="invalid_reset_token")

        user = await self._session.get(User, stored.user_id)
        if user is None:
            raise AppError("Invalid or expired reset link", status_code=400, code="invalid_reset_token")

        user.password_hash = hash_password(new_password)
        stored.used_at = now

        # Revoke all sessions after password change
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self._session.flush()

    async def delete_account(self, *, user: User, password: str) -> None:
        """Permanently delete the user and cascaded data (taste, interactions, tokens)."""
        if not verify_password(password, user.password_hash):
            raise UnauthorizedError("Password is incorrect", code="invalid_credentials")

        # Explicit cleanup for tables that may not cascade from ORM alone
        await self._session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
        await self._session.delete(user)
        await self._session.flush()
        logger.info("account_deleted user_id=%s email=%s", user.id, user.email)

    async def get_user(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def _issue_tokens(self, user: User) -> tuple[str, str]:
        access = create_access_token(user_id=user.id, settings=self._settings)
        raw_refresh = generate_refresh_token()
        refresh_row = RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            expires_at=datetime.now(UTC)
            + timedelta(days=self._settings.jwt_refresh_ttl_days),
        )
        self._session.add(refresh_row)
        await self._session.flush()
        return access, raw_refresh
