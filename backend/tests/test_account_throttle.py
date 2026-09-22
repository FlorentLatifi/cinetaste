"""Per-account throttling for credential endpoints.

Why this exists at all: the IP-keyed middleware limit is only as trustworthy as
the proxy chain, and the API origin stays publicly reachable, so a caller can
forge X-Forwarded-For and pick its own bucket. These tests pin the second
limit, which is keyed by the account under attack instead.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.throttle import _key, guard_identity, record_attempt
from app.domain.exceptions import AppError, RateLimitedError
from app.infrastructure.cache import MemoryStore


def _settings(**overrides) -> Settings:
    data = {
        "jwt_secret": "unit-test-secret-key-at-least-32-chars!",
        "database_url": "postgresql+asyncpg://u:p@localhost/x",
        "rate_limit_account_failures": 3,
        "rate_limit_account_window_seconds": 900,
    }
    data.update(overrides)
    return Settings(**data)


@pytest.fixture
def store(monkeypatch) -> MemoryStore:
    fresh = MemoryStore()
    monkeypatch.setattr("app.core.throttle.get_rate_limit_store", lambda: fresh)
    return fresh


class _BrokenStore:
    async def get(self, key: str):
        raise ConnectionError("counter store is down")

    async def hit(self, key: str, *, window_seconds: int):
        raise ConnectionError("counter store is down")


@pytest.mark.asyncio
async def test_identity_is_allowed_until_the_budget_is_spent(store) -> None:
    settings = _settings()
    for _ in range(3):
        await guard_identity("user@example.com", scope="login", settings=settings)
        await record_attempt("user@example.com", scope="login", settings=settings)

    with pytest.raises(RateLimitedError) as exc:
        await guard_identity("user@example.com", scope="login", settings=settings)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_budget_is_per_identity_and_per_scope(store) -> None:
    settings = _settings()
    for _ in range(3):
        await record_attempt("victim@example.com", scope="login", settings=settings)

    # A different account is untouched...
    await guard_identity("someone-else@example.com", scope="login", settings=settings)
    # ...and so is password reset for the same account: one abused endpoint must
    # not lock the user out of the other.
    await guard_identity("victim@example.com", scope="reset", settings=settings)

    with pytest.raises(RateLimitedError):
        await guard_identity("victim@example.com", scope="login", settings=settings)


@pytest.mark.asyncio
async def test_identity_is_normalised(store) -> None:
    """Casing and padding must not buy a fresh budget."""
    settings = _settings()
    for _ in range(3):
        await record_attempt("  User@Example.COM ", scope="login", settings=settings)

    with pytest.raises(RateLimitedError):
        await guard_identity("user@example.com", scope="login", settings=settings)


def test_key_does_not_contain_the_email() -> None:
    """Counter keys reach Redis and log lines; addresses must not."""
    key = _key("login", "someone@example.com")
    assert "someone" not in key
    assert "example.com" not in key
    assert key.startswith("rl:acct:login:")


@pytest.mark.asyncio
async def test_guard_fails_closed_when_the_store_is_down(monkeypatch) -> None:
    """Unable to count = unable to tell an attacker from a user. Refuse."""
    monkeypatch.setattr("app.core.throttle.get_rate_limit_store", _BrokenStore)
    with pytest.raises(AppError) as exc:
        await guard_identity("user@example.com", scope="login", settings=_settings())
    assert exc.value.status_code == 503
    assert not isinstance(exc.value, RateLimitedError)


@pytest.mark.asyncio
async def test_recording_fails_open_when_the_store_is_down(monkeypatch) -> None:
    """The attempt was already rejected; a lost increment is not a 500."""
    monkeypatch.setattr("app.core.throttle.get_rate_limit_store", _BrokenStore)
    await record_attempt("user@example.com", scope="login", settings=_settings())


@pytest.mark.asyncio
async def test_disabled_rate_limiting_short_circuits(store) -> None:
    settings = _settings(rate_limit_enabled=False)
    for _ in range(10):
        await record_attempt("user@example.com", scope="login", settings=settings)
    await guard_identity("user@example.com", scope="login", settings=settings)
    assert await store.get(_key("login", "user@example.com")) is None
