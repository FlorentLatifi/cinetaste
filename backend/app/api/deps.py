from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_service import AuthService
from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.domain.exceptions import ForbiddenError, UnauthorizedError
from app.infrastructure.db.models.user import User
from app.infrastructure.db.session import get_db

# The session commits when the dependency exits. scope="function" makes that
# happen *before* the response is sent. With FastAPI's default (request scope,
# >= 0.118) the exit runs after the response, so a failed commit still returned
# 200 to the client — a silently lost write. Every route must use this alias:
# two declarations of get_db with different scopes would give one request two
# separate sessions.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def get_settings_dep() -> Settings:
    return get_settings()


async def get_auth_service(
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthService:
    return AuthService(session, settings)


async def get_current_user(
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token, settings)
        user_id = UUID(payload["sub"])
        issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
    except (jwt.PyJWTError, KeyError, ValueError, OverflowError, OSError) as exc:
        raise UnauthorizedError("Invalid or expired access token") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise UnauthorizedError("User not found")
    if _issued_before_password_change(issued_at, user.password_changed_at):
        raise UnauthorizedError("Access token was issued before the password changed")
    return user


def _issued_before_password_change(
    issued_at: datetime, password_changed_at: datetime | None
) -> bool:
    """Was this access token minted before the account's password changed?

    Resetting a password revokes every refresh token, but access tokens are
    stateless — without this check a stolen one kept working for the rest of
    its TTL after the victim had locked the attacker out.

    ``iat`` is whole seconds, so the change time is truncated to match.
    Comparing against sub-second precision would reject a token issued *after*
    the change but inside the same second; being at most one second late to
    expire a token is the cheaper mistake.
    """
    if password_changed_at is None:
        return False
    changed = password_changed_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=UTC)
    return issued_at < changed.replace(microsecond=0)


async def get_verified_user(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User:
    """A user whose email is confirmed, when the deployment asks for that.

    Deliberately a second dependency rather than a check inside
    ``get_current_user``: account endpoints (/me, resend, export, delete)
    have to keep working for an unverified user, or there is no way out of
    the state. Only the product surface is gated.
    """
    if settings.require_email_verification and user.email_verified_at is None:
        raise ForbiddenError(
            "Confirm your email address to use CineTaste.",
            code="email_not_verified",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(get_verified_user)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
