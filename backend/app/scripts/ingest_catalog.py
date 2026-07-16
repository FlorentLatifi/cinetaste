"""Ingest TMDb catalog into CineTaste.

Usage (from backend/ with env loaded):

    python -m app.scripts.ingest_catalog --pages 3
    python -m app.scripts.ingest_catalog --seed-only

Always prioritizes the curated onboarding seed deck
(``app/data/onboarding_seed_deck.json``) so cold-start is not pure popularity.

Requires TMDB_API_KEY in environment or ../.env
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.application.catalog_ingest import CatalogIngestService
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.tmdb.client import TmdbClient


async def main(pages: int, include_tv: bool, *, seed_only: bool) -> int:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    logger = logging.getLogger("ingest")

    tmdb = TmdbClient(settings)
    try:
        async with async_session_factory() as session:
            service = CatalogIngestService(session, tmdb)
            if seed_only:
                stats = await service.ingest_onboarding_seed()
            else:
                stats = await service.ingest_popular(pages=pages, include_tv=include_tv)
            logger.info("Ingest complete: %s", stats)
            print(stats)
        return 0
    finally:
        await tmdb.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest TMDb catalog into CineTaste")
    parser.add_argument("--pages", type=int, default=3, help="Discover pages per media type")
    parser.add_argument("--movies-only", action="store_true")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only ingest curated onboarding seed movies (no discover pages)",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(
            asyncio.run(
                main(
                    args.pages,
                    include_tv=not args.movies_only,
                    seed_only=args.seed_only,
                )
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
