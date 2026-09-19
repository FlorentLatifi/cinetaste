from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from anyio import to_thread

from app.core.config import Settings

# bcrypt cost factor: ~250 ms per hash on typical hardware. Existing
# "$2b$12$…" hashes (written by passlib) verify unchanged.
_BCRYPT_ROUNDS = 12


def _password_bytes(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes; truncate explicitly (newer bcrypt
    # releases raise instead of truncating silently).
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(plain_password), password_hash.encode("utf-8"))
    except ValueError:  # malformed hash
        return False


# bcrypt is deliberately slow CPU work. Called directly from an async route it
# blocks the event loop, so a few concurrent logins would stall every request.
async def hash_password_async(password: str) -> str:
    return await to_thread.run_sync(hash_password, password)


async def verify_password_async(plain_password: str, password_hash: str) -> bool:
    return await to_thread.run_sync(verify_password, plain_password, password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


async def burn_password_check(plain_password: str) -> None:
    """Spend the same time as a real check, so unknown emails aren't faster."""
    await verify_password_async(plain_password, _dummy_hash())


def create_access_token(*, user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
