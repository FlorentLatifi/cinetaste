from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.domain.exceptions import ConflictError, UnauthorizedError
from app.infrastructure.db.models.user import RefreshToken, User


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
