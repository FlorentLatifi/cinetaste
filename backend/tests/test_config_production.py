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
