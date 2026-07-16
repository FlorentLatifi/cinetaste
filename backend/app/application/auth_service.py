from __future__ import annotations

import logging
import secrets
import uuid
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
from app.infrastructure.email import EmailSender, get_email_sender

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        email: EmailSender | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._email = email or get_email_sender(settings)

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
        if stored is None:
            raise UnauthorizedError("Invalid refresh token", code="invalid_refresh")

        now = datetime.now(UTC)

        # Reuse of a revoked token → compromise signal: kill the whole family.
        if stored.revoked_at is not None:
            await self._revoke_family(stored.family_id, stored.user_id, now=now)
            logger.warning(
                "refresh_token_reuse family_id=%s user_id=%s",
                stored.family_id,
                stored.user_id,
            )
            raise UnauthorizedError(
                "Session revoked due to refresh token reuse",
                code="refresh_reuse",
            )

        expires = stored.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            stored.revoked_at = now
            raise UnauthorizedError("Refresh token expired", code="refresh_expired")

        user = await self._session.get(User, stored.user_id)
        if user is None:
            raise UnauthorizedError("User not found", code="invalid_refresh")

        # Rotate within the same family
        stored.revoked_at = now
        access, new_refresh = await self._issue_tokens(user, family_id=stored.family_id)
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
        Returns the raw token only for non-production (dev UX) when email is log-only.
        """
        normalized = email.strip().lower()
        user = await self._session.scalar(select(User).where(User.email == normalized))
        if user is None:
            return None

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

        link = f"{self._settings.public_app_url.rstrip('/')}/reset-password?token={raw}"
        subject = "Reset your CineTaste password"
        body = (
            f"Hi,\n\n"
            f"We received a request to reset the password for {user.email}.\n"
            f"Open this link within {self._settings.password_reset_ttl_minutes} minutes:\n\n"
            f"{link}\n\n"
            f"If you did not request this, you can ignore this email.\n"
        )
        try:
            await self._email.send(to=user.email, subject=subject, text_body=body)
        except Exception:
            logger.exception("password_reset_email_failed user_id=%s", user.id)
            # Still keep the token so ops can recover via logs in non-prod
            if self._settings.is_production:
                raise AppError(
                    "Could not send reset email. Try again later.",
                    status_code=503,
                    code="email_unavailable",
                )

        logger.info(
            "password_reset_issued user_id=%s email=%s",
            user.id,
            user.email,
        )
        # Dev convenience: expose token when using log-only email
        if not self._settings.is_production and not (self._settings.smtp_host or "").strip():
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

        await self._session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
        await self._session.delete(user)
        await self._session.flush()
        logger.info("account_deleted user_id=%s email=%s", user.id, user.email)

    async def get_user(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def _revoke_family(self, family_id: UUID, user_id: UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self._session.flush()

    async def _issue_tokens(
        self,
        user: User,
        *,
        family_id: UUID | None = None,
    ) -> tuple[str, str]:
        access = create_access_token(user_id=user.id, settings=self._settings)
        raw_refresh = generate_refresh_token()
        fid = family_id or uuid.uuid4()
        refresh_row = RefreshToken(
            user_id=user.id,
            family_id=fid,
            token_hash=hash_token(raw_refresh),
            expires_at=datetime.now(UTC)
            + timedelta(days=self._settings.jwt_refresh_ttl_days),
        )
        self._session.add(refresh_row)
        await self._session.flush()
        return access, raw_refresh
