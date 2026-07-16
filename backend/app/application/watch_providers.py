"""Where-to-watch availability via TMDb (JustWatch data).

Providers change frequently — we fetch live and cache briefly in Redis.
Missing TMDb key / network errors degrade to an empty payload (never 500 the SPA).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.exceptions import NotFoundError
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.redis import get_redis
from app.infrastructure.tmdb.client import TmdbClient

logger = logging.getLogger(__name__)

_REGION_RE = re.compile(r"^[A-Z]{2}$")
_PROVIDER_KINDS = ("flatrate", "free", "ads", "rent", "buy")
_LOGO_BASE = "https://image.tmdb.org/t/p/w92"


@dataclass(frozen=True)
class ProviderOffer:
    provider_id: int
    name: str
    logo_url: str | None
    display_priority: int


@dataclass
class WhereToWatchResult:
    region: str
    link: str | None = None
    flatrate: list[ProviderOffer] = field(default_factory=list)
    free: list[ProviderOffer] = field(default_factory=list)
    ads: list[ProviderOffer] = field(default_factory=list)
    rent: list[ProviderOffer] = field(default_factory=list)
    buy: list[ProviderOffer] = field(default_factory=list)
    available: bool = False
    attribution: str = "Streaming data by JustWatch via TMDb"
    source: str = "tmdb"

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "link": self.link,
            "flatrate": [asdict(p) for p in self.flatrate],
            "free": [asdict(p) for p in self.free],
            "ads": [asdict(p) for p in self.ads],
            "rent": [asdict(p) for p in self.rent],
            "buy": [asdict(p) for p in self.buy],
            "available": self.available,
            "attribution": self.attribution,
            "source": self.source,
        }


def normalize_region(region: str | None, default: str = "US") -> str:
    raw = (region or default or "US").strip().upper()
    if not _REGION_RE.match(raw):
        return default.upper() if _REGION_RE.match(default.upper()) else "US"
    return raw


def parse_watch_providers_payload(
    payload: dict[str, Any],
    *,
    region: str,
) -> WhereToWatchResult:
    """Parse TMDb ``/watch/providers`` JSON for one ISO-3166-1 region."""
    region = normalize_region(region)
    results = payload.get("results") or {}
    country = results.get(region) if isinstance(results, dict) else None
    if not isinstance(country, dict):
        return WhereToWatchResult(region=region, available=False)

    def offers(kind: str) -> list[ProviderOffer]:
        rows = country.get(kind) or []
        if not isinstance(rows, list):
            return []
        out: list[ProviderOffer] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = row.get("provider_id")
            name = row.get("provider_name")
            if pid is None or not name:
                continue
            try:
                provider_id = int(pid)
            except (TypeError, ValueError):
                continue
            if provider_id in seen:
                continue
            seen.add(provider_id)
            logo_path = row.get("logo_path")
            logo_url = None
            if isinstance(logo_path, str) and logo_path:
                logo_url = (
                    logo_path
                    if logo_path.startswith("http")
                    else f"{_LOGO_BASE}{logo_path}"
                )
            raw_priority = row.get("display_priority")
            try:
                priority = int(raw_priority) if raw_priority is not None else 999
            except (TypeError, ValueError):
                priority = 999
            out.append(
                ProviderOffer(
                    provider_id=provider_id,
                    name=str(name),
                    logo_url=logo_url,
                    display_priority=priority,
                )
            )
        out.sort(key=lambda p: (p.display_priority, p.name.lower()))
        return out

    buckets = {k: offers(k) for k in _PROVIDER_KINDS}
    link = country.get("link")
    if link is not None and not isinstance(link, str):
        link = None
    available = any(buckets[k] for k in _PROVIDER_KINDS)
    return WhereToWatchResult(
        region=region,
        link=link,
        flatrate=buckets["flatrate"],
        free=buckets["free"],
        ads=buckets["ads"],
        rent=buckets["rent"],
        buy=buckets["buy"],
        available=available,
    )


class WatchProvidersService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def _cache_get(self, key: str) -> str | None:
        try:
            redis = await get_redis()
            return await redis.get(key)
        except Exception:
            logger.warning("watch_providers_cache_get_failed key=%s", key, exc_info=True)
            return None

    async def _cache_set(self, key: str, value: str, ex: int) -> None:
        try:
            redis = await get_redis()
            await redis.set(key, value, ex=ex)
        except Exception:
            logger.warning("watch_providers_cache_set_failed key=%s", key, exc_info=True)

    async def for_title(
        self,
        title_id: UUID,
        *,
        region: str | None = None,
    ) -> WhereToWatchResult:
        title = await self._session.get(Title, title_id)
        if title is None:
            raise NotFoundError("Title not found")

        reg = normalize_region(region, self._settings.watch_provider_region)
        empty = WhereToWatchResult(region=reg, available=False)

        if title.media_type not in {"movie", "tv"}:
            return empty

        cache_key = f"watch_providers:{title.media_type}:{title.external_tmdb_id}:{reg}"
        cached = await self._cache_get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                return WhereToWatchResult(
                    region=data.get("region", reg),
                    link=data.get("link"),
                    flatrate=[ProviderOffer(**p) for p in data.get("flatrate") or []],
                    free=[ProviderOffer(**p) for p in data.get("free") or []],
                    ads=[ProviderOffer(**p) for p in data.get("ads") or []],
                    rent=[ProviderOffer(**p) for p in data.get("rent") or []],
                    buy=[ProviderOffer(**p) for p in data.get("buy") or []],
                    available=bool(data.get("available")),
                    attribution=data.get(
                        "attribution", "Streaming data by JustWatch via TMDb"
                    ),
                    source=data.get("source", "cache"),
                )
            except Exception:
                logger.warning("watch_providers_cache_invalid key=%s", cache_key, exc_info=True)

        if not self._settings.tmdb_api_key:
            return empty

        try:
            tmdb = TmdbClient(self._settings)
            try:
                payload = await tmdb.get_watch_providers(
                    title.media_type, title.external_tmdb_id
                )
            finally:
                await tmdb.aclose()
        except Exception:
            logger.warning(
                "watch_providers_tmdb_failed title_id=%s tmdb_id=%s",
                title_id,
                title.external_tmdb_id,
                exc_info=True,
            )
            return empty

        result = parse_watch_providers_payload(payload, region=reg)
        result.source = "tmdb"
        await self._cache_set(
            cache_key,
            json.dumps(result.to_dict()),
            ex=self._settings.watch_provider_cache_ttl_seconds,
        )
        return result
