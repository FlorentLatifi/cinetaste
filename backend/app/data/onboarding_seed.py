"""Load and query the curated onboarding seed deck.

Source of truth: ``onboarding_seed_deck.json`` (edit that file to retune).
TMDb movie IDs are stable product identifiers; UUID primary keys in Postgres
are environment-specific and must not be hard-coded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_SEED_PATH = Path(__file__).with_name("onboarding_seed_deck.json")


@dataclass(frozen=True, slots=True)
class SeedEntry:
    tmdb_id: int
    name: str
    tier: str
    decade: int | None
    genres: tuple[str, ...]
    origin: str
    tone: str
    polarizing: bool
    bucket: str  # "primary" | "reserve"

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, bucket: str) -> SeedEntry:
        return cls(
            tmdb_id=int(raw["tmdb_id"]),
            name=str(raw.get("name") or ""),
            tier=str(raw.get("tier") or "well_known"),
            decade=int(raw["decade"]) if raw.get("decade") is not None else None,
            genres=tuple(str(g) for g in (raw.get("genres") or [])),
            origin=str(raw.get("origin") or "XX"),
            tone=str(raw.get("tone") or ""),
            polarizing=bool(raw.get("polarizing")),
            bucket=bucket,
        )


@dataclass(frozen=True, slots=True)
class OnboardingSeedDeck:
    version: int
    primary: tuple[SeedEntry, ...]
    reserve: tuple[SeedEntry, ...]

    @property
    def ordered(self) -> tuple[SeedEntry, ...]:
        """Primary first (cold-start core), then reserve (pagination fill)."""
        return self.primary + self.reserve

    def tmdb_ids(self, *, primary_only: bool = False) -> list[int]:
        entries = self.primary if primary_only else self.ordered
        return [e.tmdb_id for e in entries]

    def by_tmdb_id(self) -> dict[int, SeedEntry]:
        return {e.tmdb_id: e for e in self.ordered}


@lru_cache(maxsize=1)
def load_onboarding_seed_deck() -> OnboardingSeedDeck:
    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    primary = tuple(SeedEntry.from_dict(item, bucket="primary") for item in raw.get("primary", []))
    reserve = tuple(SeedEntry.from_dict(item, bucket="reserve") for item in raw.get("reserve", []))
    if not primary:
        raise ValueError(f"Onboarding seed deck is empty: {_SEED_PATH}")
    # Dedupe while preserving order
    seen: set[int] = set()
    clean_primary: list[SeedEntry] = []
    for e in primary:
        if e.tmdb_id in seen:
            continue
        seen.add(e.tmdb_id)
        clean_primary.append(e)
    clean_reserve: list[SeedEntry] = []
    for e in reserve:
        if e.tmdb_id in seen:
            continue
        seen.add(e.tmdb_id)
        clean_reserve.append(e)
    return OnboardingSeedDeck(
        version=int(raw.get("version") or 1),
        primary=tuple(clean_primary),
        reserve=tuple(clean_reserve),
    )


def all_seed_tmdb_ids() -> list[int]:
    """TMDb IDs to ingest preferentially so onboarding has real posters."""
    return load_onboarding_seed_deck().tmdb_ids()


def primary_seed_tmdb_ids() -> list[int]:
    return load_onboarding_seed_deck().tmdb_ids(primary_only=True)


def order_titles_by_seed(titles: list[Any], seed_ids: list[int]) -> list[Any]:
    """Stable reorder of Title objects to match curated seed order."""
    by_tmdb = {int(t.external_tmdb_id): t for t in titles}
    ordered: list[Any] = []
    for tid in seed_ids:
        title = by_tmdb.get(tid)
        if title is not None:
            ordered.append(title)
    return ordered


def pick_diverse_fallback(
    candidates: list[Any],
    *,
    limit: int,
    exclude_ids: set[Any] | None = None,
    max_per_genre: int = 2,
    max_per_decade: int = 3,
    max_per_language: int = 4,
) -> list[Any]:
    """Quality + diversity fill when curated titles are missing or exhausted.

    Prefers:
    - poster present, embedding present (caller should pre-filter)
    - high vote_count (recognizable) and solid vote_average
    - not pure popularity (avoids endless MCU/current chart bias)
    - spread across primary genre, decade, original language
    """
    skip = exclude_ids or set()
    scored: list[tuple[float, Any]] = []
    for title in candidates:
        if title.id in skip:
            continue
        if not title.poster_path:
            continue
        vote_avg = float(title.vote_average or 0.0)
        vote_count = int(title.vote_count or 0)
        popularity = float(title.popularity or 0.0)
        if vote_avg < 6.5 and vote_count < 500:
            continue
        # Recognizability without pure chart chase
        recognition = min(vote_count / 5000.0, 1.0) * 0.55
        quality = min(max(vote_avg - 6.0, 0.0) / 3.0, 1.0) * 0.35
        # Soft popularity, capped so megahits don't dominate
        pop = min(popularity / 120.0, 1.0) * 0.10
        scored.append((recognition + quality + pop, title))

    scored.sort(key=lambda x: x[0], reverse=True)

    picked: list[Any] = []
    genre_counts: dict[str, int] = {}
    decade_counts: dict[int, int] = {}
    lang_counts: dict[str, int] = {}

    def _primary_genre(t: Any) -> str:
        genres = getattr(t, "genres", None) or []
        if genres:
            return str(genres[0].name)
        return "unknown"

    def _decade(t: Any) -> int | None:
        rd = getattr(t, "release_date", None)
        if rd is None:
            return None
        return (rd.year // 10) * 10

    for _, title in scored:
        if len(picked) >= limit:
            break
        g = _primary_genre(title)
        d = _decade(title)
        lang = (title.original_language or "xx").lower()
        if genre_counts.get(g, 0) >= max_per_genre:
            continue
        if d is not None and decade_counts.get(d, 0) >= max_per_decade:
            continue
        if lang_counts.get(lang, 0) >= max_per_language:
            continue
        picked.append(title)
        genre_counts[g] = genre_counts.get(g, 0) + 1
        if d is not None:
            decade_counts[d] = decade_counts.get(d, 0) + 1
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    if len(picked) < limit:
        picked_ids = {t.id for t in picked}
        for _, title in scored:
            if title.id in picked_ids or title.id in skip:
                continue
            picked.append(title)
            if len(picked) >= limit:
                break
    return picked
