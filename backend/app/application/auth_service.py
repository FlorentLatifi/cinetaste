from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    burn_password_check,
    create_access_token,
    generate_refresh_token,
    hash_password_async,
    hash_token,
    verify_password_async,
)
from app.core.throttle import guard_identity, record_attempt
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
            password_hash=await hash_password_async(password),
            display_name=display_name.strip() if display_name else None,
        )
        self._session.add(user)
        await self._session.flush()

        access, refresh, _row_id = await self._issue_tokens(user)
        return user, access, refresh

    async def login(self, *, email: str, password: str) -> tuple[User, str, str]:
        normalized = email.strip().lower()
        # Throttle per account as well as per IP: the IP key is only as honest
        # as the proxy chain, and this one also covers attempts spread over
        # many addresses. Checked before the lookup so an exhausted budget
        # answers identically whether or not the account exists.
        await guard_identity(normalized, scope="login", settings=self._settings)

        user = await self._session.scalar(select(User).where(User.email == normalized))
        if user is None:
            # Same cost as a real check, so response time doesn't reveal accounts.
            await burn_password_check(password)
            await record_attempt(normalized, scope="login", settings=self._settings)
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")
        if not await verify_password_async(password, user.password_hash):
            await record_attempt(normalized, scope="login", settings=self._settings)
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

        # Only failures are counted, so an active user is never locked out.
        access, refresh, _row_id = await self._issue_tokens(user)
        return user, access, refresh

    async def refresh(self, *, refresh_token: str) -> tuple[User, str, str]:
        token_hash = hash_token(refresh_token)
        # Row lock: concurrent refreshes of the same token are serialised, so the
        # second one sees the first one's rotation instead of racing it.
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        if stored is None:
            raise UnauthorizedError("Invalid refresh token", code="invalid_refresh")

        now = datetime.now(UTC)

        if stored.revoked_at is not None:
            revoked_at = stored.revoked_at
            if revoked_at.tzinfo is None:
                revoked_at = revoked_at.replace(tzinfo=UTC)
            just_rotated = stored.replaced_by_id is not None and (
                now - revoked_at <= timedelta(seconds=self._settings.refresh_reuse_grace_seconds)
            )
            if just_rotated:
                # Two tabs or a double-fired effect refreshed with the same
                # cookie: issue a sibling in the same family rather than
                # treating the user's own browser as an attacker.
                user = await self._session.get(User, stored.user_id)
                if user is None:
                    raise UnauthorizedError("User not found", code="invalid_refresh")
                access, new_refresh, _row_id = await self._issue_tokens(
                    user, family_id=stored.family_id
                )
                return user, access, new_refresh

            # Reuse of a revoked token -> compromise signal: kill the whole family.
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
            raise UnauthorizedError("Refresh token expired", code="refresh_expired")

        user = await self._session.get(User, stored.user_id)
        if user is None:
            raise UnauthorizedError("User not found", code="invalid_refresh")

        # Rotate within the same family
        access, new_refresh, new_id = await self._issue_tokens(user, family_id=stored.family_id)
        stored.revoked_at = now
        stored.replaced_by_id = new_id
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

        * Email configured -> the link is emailed; nothing is returned.
        * No email, local/test -> the raw token is returned so developers can
          reset without a mail server (the SPA shows a dev link).
        * No email anywhere else -> 503. Pretending the email was sent would
          leave real users stuck, and returning or logging the token would let
          anyone reset any account.

        The configuration check runs before the user lookup, so the response
        never depends on whether the email is registered.
        """
        dev_mode = self._settings.is_dev_like and not self._settings.email_configured
        if not self._settings.email_configured and not dev_mode:
            raise AppError(
                "Password reset by email is not available on this server.",
                status_code=503,
                code="email_unavailable",
            )

        normalized = email.strip().lower()
        # Every request counts here, not just failures: the abuse is mailing a
        # real address repeatedly, which from the server's side looks like
        # success. Guard and count before the lookup so the behaviour is
        # identical for registered and unregistered addresses.
        await guard_identity(normalized, scope="reset", settings=self._settings)
        await record_attempt(normalized, scope="reset", settings=self._settings)

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
        logger.info("password_reset_issued user_id=%s", user.id)

        if dev_mode:
            return raw

        link = f"{self._settings.public_app_url.rstrip('/')}/reset-password?token={raw}"
        body = (
            "Hi,\n\n"
            "We received a request to reset your CineTaste password.\n"
            f"Open this link within {self._settings.password_reset_ttl_minutes} minutes:\n\n"
            f"{link}\n\n"
            "If you did not request this, you can ignore this email.\n"
        )
        try:
            await self._email.send(
                to=user.email, subject="Reset your CineTaste password", text_body=body
            )
        except Exception as exc:
            logger.exception("password_reset_email_failed user_id=%s", user.id)
            raise AppError(
                "Could not send reset email. Try again later.",
                status_code=503,
                code="email_unavailable",
            ) from exc
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

        user.password_hash = await hash_password_async(new_password)
        # Revoking refresh tokens below only closes the long-lived door.
        # Access tokens are stateless and stay valid for their full TTL, so
        # record the cut-off that api.deps checks them against.
        user.password_changed_at = now
        stored.used_at = now

        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self._session.flush()

    async def delete_account(self, *, user: User, password: str) -> None:
        """Permanently delete the user and cascaded data (taste, interactions, tokens)."""
        if not await verify_password_async(password, user.password_hash):
            raise UnauthorizedError("Password is incorrect", code="invalid_credentials")

        await self._session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
        await self._session.delete(user)
        await self._session.flush()
        logger.info("account_deleted user_id=%s", user.id)

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
    ) -> tuple[str, str, UUID]:
        access = create_access_token(user_id=user.id, settings=self._settings)
        raw_refresh = generate_refresh_token()
        row = RefreshToken(
            id=uuid4(),
            user_id=user.id,
            family_id=family_id or uuid4(),
            token_hash=hash_token(raw_refresh),
            expires_at=datetime.now(UTC) + timedelta(days=self._settings.jwt_refresh_ttl_days),
        )
        self._session.add(row)
        await self._session.flush()
        return access, raw_refresh, row.id
