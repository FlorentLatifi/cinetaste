"""Fetch TMDb details for the MovieLens titles into the local evaluation cache.

    python -m app.scripts.fetch_eval_metadata --movielens ~/.cache/cinetaste/eval/ml-latest-small

One detail request per MovieLens movie (~9.7k for ml-latest-small, ~8 minutes
at the default 20 requests/second). Resumable: re-running skips everything
already cached, so an interrupted or rate-limited run just continues. The
cache defaults to ``~/.cache/cinetaste/eval/tmdb_movies.jsonl`` (override with
``--cache`` or ``CINETASTE_EVAL_CACHE``) and is refused inside the repository.

Needs ``TMDB_API_KEY`` like ingest does. Where a proxy injects the TMDb token
(cloud sessions), any placeholder value works: ``TMDB_API_KEY=proxy-injected``.
No database is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from app.core.config import get_settings
from app.infrastructure.tmdb.client import TmdbClient
from app.recommendation.tmdb_eval_cache import (
    DEFAULT_CACHE_FILE,
    DEFAULT_FETCH_CONCURRENCY,
    DEFAULT_FETCH_RPS,
    ensure_outside_repo,
    fetch_into_cache,
    read_movielens_links,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--movielens", type=Path, required=True, help="ml-latest-small directory")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--max-rps", type=float, default=DEFAULT_FETCH_RPS)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_FETCH_CONCURRENCY)
    parser.add_argument("--limit", type=int, help="Fetch at most this many new titles")
    return parser


async def main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Request URLs carry the API key; keep httpx's per-request logs off.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cache = ensure_outside_repo(args.cache)
    links = read_movielens_links(args.movielens.expanduser())
    tmdb = TmdbClient(get_settings())
    try:
        report = await fetch_into_cache(
            tmdb,
            sorted(set(links.values())),
            cache,
            max_rps=args.max_rps,
            concurrency=args.concurrency,
            limit=args.limit,
        )
    finally:
        await tmdb.aclose()
    print(json.dumps({"cache": str(cache), **report.as_dict()}, indent=2))
    if report.stopped_reason:
        print(
            f"Stopped early ({report.stopped_reason}); progress is saved. Re-run later to resume.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(build_parser().parse_args())))
