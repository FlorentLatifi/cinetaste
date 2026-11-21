"""TMDb metadata for offline evaluation: a local, resumable cache.

MovieLens ships ratings but almost no content (genres, a year, sparse tags),
so evaluating on it alone never exercises directors, cast, keywords, tones or
synopses — the features production ranks on. ``links.csv`` maps every
MovieLens movie to a TMDb id; this module fetches those titles' TMDb details
once, with the production ``TmdbClient``, into a JSONL file *outside the
repository* (GroupLens forbids redistributing MovieLens, and TMDb content is
not ours to publish either).

* ``signal_inputs_from_payload`` turns a TMDb detail payload into the
  arguments ingest passes to ``build_title_signals`` — same title, keyword,
  credit and country rules. ``tests/test_tmdb_eval_cache.py`` runs the real
  ``CatalogIngestService._upsert_from_detail`` on the same payload and fails
  if the two ever disagree.
* ``trim_movie_payload`` keeps only the fields that mapping reads (plus a little
  headroom), so the cache stays small: ~3 KB per film instead of ~60 KB.
* ``fetch_into_cache`` is incremental: every finished request is appended and
  flushed at once, ids already cached are skipped, and the run stops — keeping
  what it has — on TMDb rate limiting or a run of failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.application.catalog_discovery import Throttle
from app.application.catalog_ingest import (
    CREW_JOBS,
    MAX_CAST,
    MAX_KEYWORDS,
    CatalogIngestService,
    _parse_date,
)
from app.domain.exceptions import AppError
from app.recommendation.embeddings import PersonSignal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = Path(
    os.environ.get("CINETASTE_EVAL_CACHE")
    or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "cinetaste" / "eval"
)
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "tmdb_movies.jsonl"

# Polite by default: ingest uses 25 rps and TMDb tolerates ~50 per IP.
DEFAULT_FETCH_RPS = 20.0
DEFAULT_FETCH_CONCURRENCY = 8
# Stop after this many consecutive failures that are not "not found".
MAX_CONSECUTIVE_FAILURES = 20

# Kept beyond what ingest reads today, so a future signal (composer,
# cinematographer, a deeper cast) does not need a re-fetch.
_CACHED_CAST = max(MAX_CAST, 20)
_CACHED_CREW_JOBS = CREW_JOBS | {
    "Story",
    "Novel",
    "Characters",
    "Original Music Composer",
    "Director of Photography",
}

STATUS_OK = "ok"
STATUS_NOT_FOUND = "not_found"


# ----------------------------------------------------------------- mapping


def signal_inputs_from_payload(media_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """``build_title_signals`` keyword arguments, exactly as ingest builds them.

    Mirrors ingest's ``_Detail.parse`` and ``CatalogIngestService._stage``:
    keywords are the first ``MAX_KEYWORDS`` TMDb returns, cast the first
    ``MAX_CAST`` billed (entries without an id or name skipped *after* the
    cut, as ingest does), crew limited to ``CREW_JOBS``, TV creators count as
    writers, a person is counted once per role and job, and countries come
    from ``CatalogIngestService._countries``.
    """
    tmdb_id = int(payload["id"])
    if media_type == "movie":
        name = payload.get("title") or payload.get("original_title") or f"Movie {tmdb_id}"
        release = _parse_date(payload.get("release_date"))
        runtime = payload.get("runtime")
        keyword_items = (payload.get("keywords") or {}).get("keywords") or []
    else:
        name = payload.get("name") or payload.get("original_name") or f"TV {tmdb_id}"
        release = _parse_date(payload.get("first_air_date"))
        episode_run_times = payload.get("episode_run_time") or []
        runtime = int(episode_run_times[0]) if episode_run_times else None
        keyword_items = (payload.get("keywords") or {}).get("results") or []

    credits = payload.get("credits") or {}
    people: list[PersonSignal] = []
    seen: set[tuple[int, str, str | None]] = set()

    def first_time(person_id: int, credit_type: str, job: str | None) -> bool:
        key = (person_id, credit_type, job)
        if key in seen:
            return False
        seen.add(key)
        return True

    for cast in (credits.get("cast") or [])[:MAX_CAST]:
        if not cast.get("id") or not cast.get("name"):
            continue
        order = cast.get("order")
        billing = int(order) if order is not None else None
        if first_time(int(cast["id"]), "cast", None):
            people.append(PersonSignal(name=cast["name"], role="cast", billing_order=billing))

    for crew in credits.get("crew") or []:
        job = (crew.get("job") or "").strip()
        if job not in CREW_JOBS or not crew.get("id") or not crew.get("name"):
            continue
        if first_time(int(crew["id"]), "crew", job):
            role = "director" if job == "Director" else "writer"
            people.append(PersonSignal(name=crew["name"], role=role))

    for creator in payload.get("created_by") or []:
        if not creator.get("id") or not creator.get("name"):
            continue
        if first_time(int(creator["id"]), "crew", "Creator"):
            people.append(PersonSignal(name=creator["name"], role="writer"))

    return {
        "name": name,
        "overview": payload.get("overview"),
        "genres": [g["name"] for g in payload.get("genres") or []],
        "keywords": [k["name"] for k in keyword_items[:MAX_KEYWORDS]],
        "people": people,
        "media_type": media_type,
        "release_year": release.year if release else None,
        "runtime": runtime,
        "original_language": payload.get("original_language"),
        "countries": CatalogIngestService._countries(dict(payload)),
    }


def _trim_collection(raw: Any) -> dict[str, Any] | None:
    """``belongs_to_collection`` reduced to what ingest keeps (``id``, ``name``)."""
    if not isinstance(raw, Mapping):
        return None
    return {"id": raw.get("id"), "name": raw.get("name")}


def trim_movie_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The subset of a movie detail payload the mapping above (and ranking) reads."""
    credits = payload.get("credits") or {}
    keywords = (payload.get("keywords") or {}).get("keywords") or []
    return {
        "id": payload.get("id"),
        "imdb_id": payload.get("imdb_id"),
        "title": payload.get("title"),
        "original_title": payload.get("original_title"),
        "overview": payload.get("overview"),
        "release_date": payload.get("release_date"),
        "runtime": payload.get("runtime"),
        "original_language": payload.get("original_language"),
        "popularity": payload.get("popularity"),
        "vote_average": payload.get("vote_average"),
        "vote_count": payload.get("vote_count"),
        "genres": [{"id": g.get("id"), "name": g.get("name")} for g in payload.get("genres") or []],
        "keywords": {"keywords": [{"id": k.get("id"), "name": k.get("name")} for k in keywords]},
        "production_countries": [
            {"iso_3166_1": c.get("iso_3166_1")} for c in payload.get("production_countries") or []
        ],
        "origin_country": payload.get("origin_country") or [],
        # The franchise (TMDb collection) for the per-collection slate cap.
        "belongs_to_collection": _trim_collection(payload.get("belongs_to_collection")),
        "credits": {
            "cast": [
                {"id": c.get("id"), "name": c.get("name"), "order": c.get("order")}
                for c in (credits.get("cast") or [])[:_CACHED_CAST]
            ],
            "crew": [
                {"id": c.get("id"), "name": c.get("name"), "job": c.get("job")}
                for c in credits.get("crew") or []
                if (c.get("job") or "").strip() in _CACHED_CREW_JOBS
            ],
        },
    }


# ------------------------------------------------------------------- cache


def ensure_outside_repo(path: Path) -> Path:
    """Refuse cache/data paths inside the repository (nothing here may be committed)."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError(
            f"{resolved} is inside the repository ({REPO_ROOT}). The TMDb cache and "
            "MovieLens files must live outside it — GroupLens forbids redistribution. "
            f"Use the default ({DEFAULT_CACHE_FILE}) or set CINETASTE_EVAL_CACHE."
        )
    return resolved


def iter_cache(path: Path) -> Iterator[dict[str, Any]]:
    """Records in file order; a torn last line (interrupted write) is skipped."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("tmdb_eval_cache skipping unreadable line in %s", path)


def load_cache(path: Path) -> dict[int, dict[str, Any]]:
    """tmdb_id → latest record (``status`` ok with ``payload``, or not_found)."""
    records: dict[int, dict[str, Any]] = {}
    for record in iter_cache(path):
        try:
            records[int(record["tmdb_id"])] = record
        except (KeyError, TypeError, ValueError):
            continue
    return records


def read_movielens_links(directory: Path) -> dict[str, int]:
    """MovieLens movieId → TMDb id (rows without a TMDb id are dropped)."""
    import csv

    path = directory / "links.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Download ml-latest-small from "
            "https://grouplens.org/datasets/movielens/ and unzip it outside the repository."
        )
    links: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("tmdbId") or "").strip()
            if raw.isdigit() and int(raw) > 0:
                links[row["movieId"]] = int(raw)
    return links


# ------------------------------------------------------------------- fetch


@dataclass
class FetchReport:
    requested: int = 0
    already_cached: int = 0
    fetched: int = 0
    not_found: int = 0
    failed: int = 0
    stopped_reason: str | None = None
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "already_cached": self.already_cached,
            "fetched": self.fetched,
            "not_found": self.not_found,
            "failed": self.failed,
            "stopped_reason": self.stopped_reason,
            "seconds": round(self.seconds, 1),
            "sample_failures": self.failures[:5],
        }


async def fetch_into_cache(
    tmdb: Any,
    tmdb_ids: Iterable[int],
    cache_path: Path,
    *,
    max_rps: float = DEFAULT_FETCH_RPS,
    concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    limit: int | None = None,
    progress_every: int = 500,
) -> FetchReport:
    """Fetch movie details for ids not cached yet, appending each as it lands.

    ``tmdb`` is a ``TmdbClient`` (anything with ``async get_movie(id)``). Stops
    early — keeping every record already written — when TMDb rate-limits
    after the client's own retries, or after ``MAX_CONSECUTIVE_FAILURES``
    failures in a row. Re-running resumes where it stopped.
    """
    cache_path = ensure_outside_repo(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    report = FetchReport()
    started = time.monotonic()

    cached = load_cache(cache_path)
    wanted = list(dict.fromkeys(int(i) for i in tmdb_ids))
    report.requested = len(wanted)
    todo = [i for i in wanted if i not in cached]
    report.already_cached = len(wanted) - len(todo)
    if limit is not None:
        todo = todo[: max(limit, 0)]
    if not todo:
        report.seconds = time.monotonic() - started
        return report

    throttle = Throttle(max_rps)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for tmdb_id in todo:
        queue.put_nowait(tmdb_id)
    consecutive_failures = 0
    stop = asyncio.Event()

    with cache_path.open("a", encoding="utf-8") as sink:

        def write(record: dict[str, Any]) -> None:
            sink.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            sink.flush()

        async def worker() -> None:
            nonlocal consecutive_failures
            while not stop.is_set():
                try:
                    tmdb_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await throttle.wait()
                fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
                try:
                    payload = await tmdb.get_movie(tmdb_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        write(
                            {"tmdb_id": tmdb_id, "status": STATUS_NOT_FOUND, "fetched_at": fetched_at}
                        )
                        report.not_found += 1
                        consecutive_failures = 0
                        continue
                    report.failed += 1
                    consecutive_failures += 1
                    report.failures.append(f"{tmdb_id}: HTTP {exc.response.status_code}")
                except AppError as exc:
                    if exc.code in {"tmdb_rate_limited", "tmdb_unauthorized"}:
                        report.stopped_reason = exc.code
                        stop.set()
                        return
                    report.failed += 1
                    consecutive_failures += 1
                    report.failures.append(f"{tmdb_id}: {exc.code}")
                else:
                    write(
                        {
                            "tmdb_id": tmdb_id,
                            "status": STATUS_OK,
                            "fetched_at": fetched_at,
                            "payload": trim_movie_payload(payload),
                        }
                    )
                    report.fetched += 1
                    consecutive_failures = 0
                    done = report.fetched + report.not_found
                    if progress_every and done % progress_every == 0:
                        logger.info(
                            "tmdb_eval_fetch progress=%d/%d failed=%d", done, len(todo), report.failed
                        )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    report.stopped_reason = "too_many_consecutive_failures"
                    stop.set()
                    return

        await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))

    report.seconds = time.monotonic() - started
    return report
