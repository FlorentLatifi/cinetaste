from fastapi import Response

from app.core.config import Settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    refresh_cookie_path,
    set_refresh_cookie,
)


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        api_prefix="/api/v1",
        app_env="local",
    )
    base.update(kwargs)
    return Settings(**base)


def test_refresh_cookie_path_scoped_to_auth() -> None:
    s = _settings()
    assert refresh_cookie_path(s) == "/api/v1/auth"


def test_set_refresh_cookie_httponly_local() -> None:
    s = _settings(app_env="local")
    response = Response()
    set_refresh_cookie(response, "raw-refresh-token-value", s)
    header = response.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in header
    assert "HttpOnly" in header or "httponly" in header.lower()
    assert "raw-refresh-token-value" in header
    # Local: Lax, not forcing Secure
    assert "lax" in header.lower() or "SameSite=lax" in header or "samesite=lax" in header.lower()


def test_set_refresh_cookie_production_samesite_none() -> None:
    s = _settings(app_env="production", cors_origins="https://cinetaste.vercel.app", jwt_secret="x" * 48)
    response = Response()
    set_refresh_cookie(response, "prod-refresh", s)
    header = response.headers.get("set-cookie", "")
    assert "none" in header.lower()
    assert "secure" in header.lower()


def test_clear_refresh_cookie() -> None:
    s = _settings()
    response = Response()
    clear_refresh_cookie(response, s)
    header = response.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in header
