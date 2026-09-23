import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _base(**overrides):
    data = {
        "app_env": "production",
        "app_debug": False,
        "jwt_secret": "x" * 48,
        "database_url": "postgresql+asyncpg://prod:strong@db:5432/cinetaste",
        "redis_url": "redis://redis:6379/0",
        "cors_origins": "https://cinetaste.vercel.app",
    }
    data.update(overrides)
    return data


def test_production_accepts_strong_config() -> None:
    settings = Settings(**_base())
    assert settings.is_production
    assert settings.cors_origin_list == ["https://cinetaste.vercel.app"]


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError):
        Settings(**_base(app_debug=True))


def test_production_rejects_weak_jwt() -> None:
    with pytest.raises(ValidationError):
        Settings(**_base(jwt_secret="local-dev-only-change-me-to-a-long-random-string-32chars"))


def test_production_rejects_default_db_password() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **_base(
                database_url="postgresql+asyncpg://cinetaste:cinetaste@db:5432/cinetaste",
            )
        )


def test_production_normalizes_postgres_scheme() -> None:
    settings = Settings(
        **_base(database_url="postgres://prod:strong@db:5432/cinetaste"),
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_production_rejects_localhost_cors() -> None:
    with pytest.raises(ValidationError):
        Settings(**_base(cors_origins="http://localhost:5173"))


def test_app_env_aliases_are_normalized() -> None:
    assert Settings(**_base(app_env="prod")).app_env == "production"
    local = Settings(**{**_base(), "app_env": "development", "cors_origins": "http://localhost:5173"})
    assert local.app_env == "local"
    assert local.is_dev_like


def test_unknown_app_env_is_rejected() -> None:
    """A typo must not silently disable production safety checks."""
    with pytest.raises(ValidationError):
        Settings(**_base(app_env="prod-eu"))


def test_production_email_requires_public_https_url() -> None:
    with pytest.raises(ValidationError):
        Settings(**_base(smtp_host="smtp.example.com", public_app_url="http://localhost:5173"))
    ok = Settings(**_base(smtp_host="smtp.example.com", public_app_url="https://cinetaste.app"))
    assert ok.email_configured


def test_local_allows_dev_defaults() -> None:
    settings = Settings(
        app_env="local",
        app_debug=True,
        jwt_secret="local-dev-only-change-me-to-a-long-random-string-32chars",
        database_url="postgresql+asyncpg://cinetaste:cinetaste@localhost:5432/cinetaste",
        redis_url="redis://localhost:6379/0",
        cors_origins="http://localhost:5173",
    )
    assert not settings.is_production


def test_production_rejects_wildcard_cors_origin() -> None:
    """``*`` plus credentials is not a wildcard, it is "allow every site".

    Starlette answers a credentialed request by echoing the caller's Origin when
    allow_origins is ``*``, so any page could read authenticated responses. The
    other CORS mistakes were already rejected here; this one was not.
    """
    with pytest.raises(ValidationError):
        Settings(**_base(cors_origins="*"))

    with pytest.raises(ValidationError):
        Settings(**_base(cors_origins="https://cinetaste.vercel.app,*"))


def test_jwt_algorithm_is_restricted_to_hmac_sha2() -> None:
    """A free-form string would let a deploy pick an algorithm the key is wrong for."""
    Settings(**_base(jwt_algorithm="HS512"))
    with pytest.raises(ValidationError):
        Settings(**_base(jwt_algorithm="none"))
    with pytest.raises(ValidationError):
        Settings(**_base(jwt_algorithm="RS256"))
