"""Ingest popular movies/TV from TMDb.

Usage (from backend/ with env loaded):

    python -m app.scripts.ingest_catalog --pages 3

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


async def main(pages: int, include_tv: bool) -> int:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    logger = logging.getLogger("ingest")

    tmdb = TmdbClient(settings)
    try:
        async with async_session_factory() as session:
            service = CatalogIngestService(session, tmdb)
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
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.pages, include_tv=not args.movies_only)))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
