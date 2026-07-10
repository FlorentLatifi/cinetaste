from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {
    "local-dev-only-change-me-to-a-long-random-string-32chars",
    "change-me-to-a-long-random-string",
    "ci-test-secret-key-at-least-32-characters-long",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CineTaste"
    app_env: str = "local"
    app_debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173"

    jwt_secret: str = Field(min_length=32)
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    jwt_algorithm: str = "HS256"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    rec_slate_size: int = 20
    rec_cache_ttl_seconds: int = 600
    rec_mmr_lambda: float = 0.7

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_auth_requests: int = 20
    rate_limit_auth_window_seconds: int = 60

    # Comma-separated hostnames allowed in production (optional)
    trusted_hosts: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def strip_cors(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Hosts often inject postgres:// — SQLAlchemy async needs postgresql+asyncpg://."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Managed Postgres (Render/Railway) usually requires TLS.
        local = any(h in value for h in ("localhost", "127.0.0.1", "@db:", "@db/"))
        if not local and "ssl=" not in value and "sslmode=" not in value:
            sep = "&" if "?" in value else "?"
            value = f"{value}{sep}ssl=require"
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        if not self.trusted_hosts.strip():
            return ["*"]
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if not self.is_production:
            return self

        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")

        if self.jwt_secret in _WEAK_SECRETS or "change-me" in self.jwt_secret.lower():
            raise ValueError("JWT_SECRET is weak or default — set a strong unique secret in production")

        if len(self.jwt_secret) < 48:
            raise ValueError("JWT_SECRET should be at least 48 characters in production")

        if not self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must be set in production")

        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origin_list):
            raise ValueError("CORS_ORIGINS must not include localhost in production")

        if self.database_url.startswith("postgresql+asyncpg://cinetaste:cinetaste@"):
            raise ValueError("Default local DATABASE_URL credentials are not allowed in production")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
