from app.core.config import Settings
from app.core.observability import capture_exception, init_observability, sentry_enabled


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        sentry_dsn="",
    )
    base.update(kwargs)
    return Settings(**base)


def test_sentry_disabled_without_dsn() -> None:
    init_observability(_settings(sentry_dsn=""))
    assert sentry_enabled() is False
    # Must not raise
    capture_exception(RuntimeError("noop"))


def test_sentry_disabled_with_whitespace_dsn() -> None:
    init_observability(_settings(sentry_dsn="   "))
    assert sentry_enabled() is False
