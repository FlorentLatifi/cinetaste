"""Ingest TMDb catalog into CineTaste.

Usage (from backend/ with env loaded):

    python -m app.scripts.ingest_catalog --dry-run    # plan only: TMDb discover, no database
    python -m app.scripts.ingest_catalog              # defaults: ~4-4.7k titles (movies + smaller TV share)
    python -m app.scripts.ingest_catalog --no-discovery   # mainstream only (~800 titles)
    python -m app.scripts.ingest_catalog --languages ko,ja --decades none
    python -m app.scripts.ingest_catalog --seed-only

Passes (priority order): mainstream popularity/vote-count (``--pages``), top-rated
above a vote floor (``--rated-pages``), top-rated per original language
(``--languages``), top-rated per decade (``--decades``), then a smaller TV share.
Every depth flag takes 0 to switch that pass off.

Always prioritizes the curated onboarding seed deck
(``app/data/onboarding_seed_deck.json``) so cold-start is not pure popularity.

Re-running is safe and resumable: titles are upserted by TMDb id, and titles that
only a discovery pass found are skipped once stored (``--refresh-existing``
re-fetches them).

Requires TMDB_API_KEY in environment or ../.env
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from app.application.catalog_discovery import (
    DEFAULT_DECADES,
    DEFAULT_LANGUAGES,
    DiscoveryPlan,
    Throttle,
    build_passes,
    collect_targets,
)
from app.application.catalog_ingest import CatalogIngestService
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.tmdb.client import TmdbClient

DEFAULT_MAX_RPS = 25.0
_defaults = DiscoveryPlan()


def _csv(value: str) -> list[str]:
    value = value.strip().lower()
    if value in {"", "none"}:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest TMDb catalog into CineTaste")
    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="Mainstream discover pages (20 titles each) per sort order and media type",
    )
    parser.add_argument("--movies-only", action="store_true")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only ingest curated onboarding seed movies (no discover pages)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the discover passes against TMDb and report counts; no database, no detail requests",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip every discovery pass below (mainstream pages only)",
    )
    parser.add_argument(
        "--rated-pages",
        type=int,
        default=_defaults.rated_pages,
        help="Pages of top-rated movies (vote_average.desc); 0 = off",
    )
    parser.add_argument("--rated-min-votes", type=int, default=_defaults.rated_min_votes)
    parser.add_argument(
        "--languages",
        default=",".join(DEFAULT_LANGUAGES),
        help="Comma-separated ISO 639-1 codes for top-rated non-English passes, or 'none'",
    )
    parser.add_argument("--language-pages", type=int, default=_defaults.language_pages)
    parser.add_argument("--language-min-votes", type=int, default=_defaults.language_min_votes)
    parser.add_argument(
        "--decades",
        default=",".join(str(d) for d in DEFAULT_DECADES),
        help="Comma-separated decade starts (e.g. 1970,1980) for top-rated per-decade passes, or 'none'",
    )
    parser.add_argument("--decade-pages", type=int, default=_defaults.decade_pages)
    parser.add_argument("--decade-min-votes", type=int, default=_defaults.decade_min_votes)
    parser.add_argument(
        "--tv-rated-pages",
        type=int,
        default=_defaults.tv_rated_pages,
        help="Pages of top-rated TV; 0 = off",
    )
    parser.add_argument(
        "--tv-language-pages",
        type=int,
        default=_defaults.tv_language_pages,
        help="Pages of top-rated TV per --languages code; 0 = off",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Re-fetch titles already in the catalog even if only a discovery pass found them",
    )
    parser.add_argument(
        "--max-rps",
        type=float,
        default=DEFAULT_MAX_RPS,
        help="Cap on TMDb requests per second (TMDb allows ~50); 0 = uncapped",
    )
    return parser


def plan_from_args(args: argparse.Namespace) -> DiscoveryPlan:
    """Validated discovery plan; raises ValueError on a bad flag value."""
    if args.no_discovery:
        return DiscoveryPlan.disabled()
    try:
        decades = tuple(int(d) for d in _csv(args.decades))
    except ValueError as exc:
        raise ValueError(f"--decades must be comma-separated years, got {args.decades!r}") from exc
    return DiscoveryPlan(
        rated_pages=args.rated_pages,
        rated_min_votes=args.rated_min_votes,
        decades=decades,
        decade_pages=args.decade_pages,
        decade_min_votes=args.decade_min_votes,
        languages=tuple(_csv(args.languages)),
        language_pages=args.language_pages,
        language_min_votes=args.language_min_votes,
        tv_rated_pages=args.tv_rated_pages,
        tv_language_pages=args.tv_language_pages,
    )


async def plan_only(tmdb: TmdbClient, args: argparse.Namespace, plan: DiscoveryPlan) -> dict[str, Any]:
    """Discover pages only. Detail requests would add one per unique title."""
    passes = build_passes(pages=args.pages, include_tv=not args.movies_only, plan=plan)
    collected = await collect_targets(tmdb, passes, throttle=Throttle(args.max_rps))
    movies = sum(1 for media, _ in collected.items if media == "movie")
    tv = len(collected.items) - movies
    return {
        "dry_run": True,
        "unique_movies": movies,
        "unique_tv": tv,
        "discover_requests": collected.discover_requests,
        "detail_requests_if_empty_catalog": len(collected.items),
        "failed_passes": collected.failed_passes,
        "passes": [
            {
                "pass": r.label,
                "pages": f"{r.pages_fetched}/{r.pages_requested}",
                "seen": r.titles_seen,
                "new_unique": r.new_unique,
            }
            for r in collected.reports
        ],
    }


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    logger = logging.getLogger("ingest")
    plan = plan_from_args(args)

    tmdb = TmdbClient(settings)
    try:
        if args.dry_run:
            stats = await plan_only(tmdb, args, plan)
        else:
            async with async_session_factory() as session:
                service = CatalogIngestService(session, tmdb, max_rps=args.max_rps)
                if args.seed_only:
                    stats = await service.ingest_onboarding_seed()
                else:
                    stats = await service.ingest_popular(
                        pages=args.pages,
                        include_tv=not args.movies_only,
                        discovery=plan,
                        refresh_existing=args.refresh_existing,
                    )
        logger.info("Ingest complete: %s", stats)
        print(stats)
        return 0
    finally:
        await tmdb.aclose()


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(main(parsed)))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
