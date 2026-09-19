from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Accepted APP_ENV spellings → canonical environment.
_ENV_ALIASES = {
    "local": "local",
    "dev": "local",
    "development": "local",
    "test": "test",
    "testing": "test",
    "ci": "test",
    "staging": "staging",
    "stage": "staging",
    "prod": "production",
    "production": "production",
}

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
    # local | test | staging | production (aliases accepted, anything else fails).
    app_env: str = "local"
    app_debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173"

    jwt_secret: str = Field(min_length=32)
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    jwt_algorithm: str = "HS256"
    # A refresh token presented again within this many seconds of being rotated
    # (two tabs, a double-fired effect) gets a sibling token instead of being
    # treated as theft, which would revoke the whole session family.
    refresh_reuse_grace_seconds: int = Field(default=20, ge=0, le=120)

    database_url: str
    # Optional. Empty → in-process cache/rate limits (fine for one process).
    redis_url: str = ""

    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    # Where-to-watch (TMDb / JustWatch). ISO-3166-1 alpha-2 default region.
    watch_provider_region: str = "US"
    watch_provider_cache_ttl_seconds: int = 43_200  # 12h

    rec_slate_size: int = 20
    rec_cache_ttl_seconds: int = 600
    rec_mmr_lambda: float = 0.7
    # Soft quota of exploration / stretch picks reserved in each For You slate
    rec_exploration_slots: int = 3
    # Candidate generation (pgvector ANN + popularity exploration pool)
    rec_ann_candidates: int = 250
    rec_popular_candidates: int = 120
    rec_use_ann: bool = True
    # Append-only For You impression log (offline eval). Fail-open if write fails.
    rec_log_impressions: bool = True
    # Taste drifts: an interaction loses half its influence after this many days
    # (0 disables decay).
    taste_half_life_days: float = Field(default=365.0, ge=0)

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_auth_requests: int = 20
    rate_limit_auth_window_seconds: int = 60

    # Comma-separated hostnames allowed in production (optional)
    trusted_hosts: str = ""

    # Reverse proxies in front of the API that append to X-Forwarded-For
    # (0 = ignore the header). Render alone: 1. Vercel rewrite → Render: 2.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    # Force Secure cookies even outside production (e.g. https:// local tunnels)
    cookie_secure: bool = False
    # "lax" when the SPA reaches the API on the same site (Vite proxy locally,
    # Vercel rewrite in production). "none" only if the browser calls the API on
    # another site directly — third-party cookies are blocked by Safari.
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Observability (optional — leave empty to disable)
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    sentry_release: str = ""

    # Password reset (token TTL). Real email delivery is optional for MVP.
    password_reset_ttl_minutes: int = 60
    # Public frontend origin used to build reset links in logs (dev/staging).
    public_app_url: str = "http://localhost:5173"

    # Optional SMTP — if smtp_host is empty, password-reset emails are log-only.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        key = str(value).strip().lower()
        if key not in _ENV_ALIASES:
            allowed = ", ".join(sorted(set(_ENV_ALIASES.values())))
            raise ValueError(f"APP_ENV must be one of {allowed} (got {value!r})")
        return _ENV_ALIASES[key]

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
        return self.app_env == "production"

    @property
    def is_dev_like(self) -> bool:
        """Local development and automated tests (dev conveniences allowed)."""
        return self.app_env in {"local", "test"}

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host.strip())

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if not self.is_production:
            return self

        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")

        if self.jwt_secret in _WEAK_SECRETS or "change-me" in self.jwt_secret.lower():
            raise ValueError("JWT_SECRET is weak or default — set a strong unique secret in production")

        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET should be at least 32 characters in production")

        if not self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must be set in production")

        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origin_list):
            raise ValueError("CORS_ORIGINS must not include localhost in production")

        if self.database_url.startswith("postgresql+asyncpg://cinetaste:cinetaste@"):
            raise ValueError("Default local DATABASE_URL credentials are not allowed in production")

        if self.email_configured and (
            not self.public_app_url.startswith("https://") or "localhost" in self.public_app_url
        ):
            raise ValueError(
                "PUBLIC_APP_URL must be the public https:// SPA URL when email is enabled "
                "(password-reset links point there)"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
