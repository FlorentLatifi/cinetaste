"""Profile catalog ingest writes against a LOCAL database with simulated network latency.

Never run this at production: it empties the catalog tables of the database it is pointed at.

    # one-time: a throwaway database (docker compose: the "db" service)
    createdb cinetaste_bench && psql cinetaste_bench -c "CREATE EXTENSION vector"
    export DATABASE_URL=postgresql+asyncpg://cinetaste:cinetaste@localhost:5432/cinetaste_bench
    python -m alembic upgrade head

    # real TMDb detail payloads (needs TMDB_API_KEY), kept outside the repository
    python tools/profile_catalog_ingest.py --fetch ~/cinetaste-ingest-payloads.jsonl

    python tools/profile_catalog_ingest.py --payloads ~/cinetaste-ingest-payloads.jsonl --rtt-ms 150

Why: the production ingest runs on a GitHub runner against hosted Postgres, so every
statement pays a network round trip. On a local database the same code looks fast, which
hides the cost. This tool puts a TCP proxy between the ingest and Postgres that delays
each packet by half of ``--rtt-ms`` in each direction, then reports per scenario:

* seconds per title (wall clock for ``CatalogIngestService._ingest_ids``, the batch loop
  that writes titles and commits per batch; TMDb is served from the payload file, so this
  is database time plus Python),
* SQL statements per title (SQLAlchemy cursor executions, by kind),
* network round trips per title (turnarounds counted at the proxy: the client sending
  after the server last spoke). This can differ from the statement count: asyncpg
  prepares a statement it has not seen on the connection before (one extra round trip),
  BEGIN and pool pre-ping are not cursor executions, and an ``executemany`` is pipelined
  into one round trip however many rows it sends.

Scenarios, in order, on one catalog that is emptied first (each with a fresh service,
as a new run would have):

* ``cold``: the onboarding seed into the empty catalog (every person / keyword is new),
* ``refresh``: the seed again. Titles, people and keywords all exist. This is the shape
  of a ``seed-only`` run against production.
* ``new``: the other payloads into a catalog that holds the seed. People and keywords
  are partly known. This is the shape of a ``popular`` run.

Calibration: time is round trips x RTT almost exactly (CPU is a few ms per title).
Catalog run #8 (seed-only, 2026-11-21) took 8.5 s per title at ~57 round trips each,
so the runner-to-Neon round trip is about 150 ms, hence the default.

Safety: refuses any DATABASE_URL whose host is not loopback / ``db`` and whose database
name does not end in ``_bench`` or ``_test``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import re
import socket
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

# ----------------------------------------------------------------- safety


def _guard_database(url_text: str) -> None:
    url = make_url(url_text)
    host = (url.host or "").lower()
    name = url.database or ""
    if host not in {"localhost", "127.0.0.1", "::1", "db"}:
        sys.exit(f"refusing: host {host!r} is not local. This tool is for a local stack only.")
    if not name.endswith(("_bench", "_test")):
        sys.exit(
            f"refusing: database {name!r} must end in _bench or _test "
            "(this tool empties the catalog tables)."
        )


# ------------------------------------------------------------ latency proxy


def _run_proxy(
    listen_port: int,
    target: tuple[str, int],
    one_way_s: float,
    round_trips: Any,
    ready: Any,
) -> None:
    """Forward TCP to Postgres, delaying every chunk by ``one_way_s`` in each direction.

    Order is preserved per direction (a queue with release times), so this models
    latency, not reordering or bandwidth. Runs in its own process so the ingest's Python
    work cannot hold up the proxy's timers.
    """

    async def pipe(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        side: str,
        last: dict[str, str],
    ) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()

        async def pump() -> None:
            while True:
                data = await reader.read(65536)
                # A round trip is a turnaround: the client sending after the server last
                # spoke. Pipelined messages (asyncpg executemany) count once, not per send.
                if data and side == "client" and last.get("side") != "client":
                    with round_trips.get_lock():
                        round_trips.value += 1
                if data:
                    last["side"] = side
                queue.put_nowait((loop.time() + one_way_s, data))
                if not data:
                    return

        async def release() -> None:
            while True:
                due, data = await queue.get()
                wait = due - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                if not data:
                    writer.close()
                    return
                writer.write(data)
                await writer.drain()

        try:
            await asyncio.gather(pump(), release())
        except (ConnectionError, OSError):
            writer.close()

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        server_r, server_w = await asyncio.open_connection(*target)
        server_w.transport.get_extra_info("socket").setsockopt(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
        )
        client_w.transport.get_extra_info("socket").setsockopt(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
        )
        last: dict[str, str] = {}
        await asyncio.gather(
            pipe(client_r, server_w, "client", last),
            pipe(server_r, client_w, "server", last),
            return_exceptions=True,
        )

    async def main() -> None:
        server = await asyncio.start_server(handle, "127.0.0.1", listen_port)
        ready.set()
        async with server:
            await server.serve_forever()

    asyncio.run(main())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ payloads


async def fetch_payloads(out: Path, *, movie_pages: int, tv_pages: int) -> None:
    """Seed deck + popular / most-voted discover pages, as ``{"media_type", "payload"}`` lines."""
    from app.core.config import get_settings
    from app.data.onboarding_seed import all_seed_tmdb_ids
    from app.infrastructure.tmdb.client import TmdbClient
    from app.recommendation.tmdb_eval_cache import ensure_outside_repo

    ensure_outside_repo(out)
    tmdb = TmdbClient(get_settings())
    try:
        targets = [("movie", tmdb_id) for tmdb_id in all_seed_tmdb_ids()]
        seen = set(targets)
        for media_type, pages in (("movie", movie_pages), ("tv", tv_pages)):
            for sort_by in ("popularity.desc", "vote_count.desc"):
                for page in range(1, pages + 1):
                    for item in await tmdb.discover(media_type, page=page, sort_by=sort_by):
                        ident = (media_type, int(item["id"]))
                        if ident not in seen:
                            seen.add(ident)
                            targets.append(ident)
        semaphore = asyncio.Semaphore(6)

        async def detail(media_type: str, tmdb_id: int) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    get = tmdb.get_movie if media_type == "movie" else tmdb.get_tv
                    return {"media_type": media_type, "payload": await get(tmdb_id)}
                except Exception as exc:  # noqa: BLE001
                    print(f"skip {media_type} {tmdb_id}: {exc}", file=sys.stderr)
                    return None

        records = [r for r in await asyncio.gather(*(detail(m, i) for m, i in targets)) if r]
    finally:
        await tmdb.aclose()
    out.write_text("".join(json.dumps(r) + "\n" for r in records))
    print(f"wrote {len(records)} payloads to {out}")


class _FileTmdb:
    """Serves detail payloads from the file; no network."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._details = {(r["media_type"], int(r["payload"]["id"])): r["payload"] for r in records}

    async def get_genres(self, media_type: str) -> list[dict[str, Any]]:
        genres: dict[int, str] = {}
        for (media, _), payload in self._details.items():
            if media == media_type:
                for g in payload.get("genres") or []:
                    genres[int(g["id"])] = g["name"]
        return [{"id": i, "name": n} for i, n in sorted(genres.items())]

    async def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        return self._details[("movie", tmdb_id)]

    async def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        return self._details[("tv", tmdb_id)]


# ------------------------------------------------------------------ measuring

_KIND = re.compile(
    r"^\s*(SELECT|INSERT INTO|UPDATE|DELETE FROM|SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT)\s*(\w+)?",
    re.I,
)


def _kind(statement: str) -> str:
    match = _KIND.match(statement)
    if not match:
        return statement.split(None, 1)[0].upper() if statement.strip() else "?"
    verb, table = match.group(1).upper(), (match.group(2) or "")
    if verb.endswith("SAVEPOINT"):
        return verb
    if verb == "SELECT":
        found = re.search(r"\bFROM\s+(\w+)", statement, re.I)
        table = found.group(1) if found else ""
    return f"{verb} {table}".strip()


async def _truncate(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE credits, title_keywords, title_genres, keywords, people, "
                "genres, titles RESTART IDENTITY CASCADE"
            )
        )


async def _scenario(
    name: str,
    factory: Any,
    tmdb: _FileTmdb,
    targets: list[tuple[str, int]],
    counter: Counter[str],
    round_trips: Any,
    batch_size: int,
) -> dict[str, Any]:
    from app.application.catalog_ingest import CatalogIngestService

    async with factory() as session:
        service = CatalogIngestService(session, tmdb, batch_size=batch_size)  # type: ignore[arg-type]
        await service._ensure_genres()  # once per run, outside the per-title loop
        counter.clear()
        round_trips_before = round_trips.value
        started = time.perf_counter()
        stats = await service._ingest_ids(targets)
        elapsed = time.perf_counter() - started
    titles = max(1, len(targets))
    return {
        "scenario": name,
        "titles": len(targets),
        "created": stats["created"],
        "updated": stats["updated"],
        "failed": stats["failed"],
        "seconds": round(elapsed, 2),
        "s_per_title": round(elapsed / titles, 3),
        "statements_per_title": round(sum(counter.values()) / titles, 1),
        "round_trips_per_title": round((round_trips.value - round_trips_before) / titles, 1),
        "by_kind_per_title": {kind: round(n / titles, 2) for kind, n in counter.most_common(12)},
    }


async def profile(args: argparse.Namespace) -> list[dict[str, Any]]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.data.onboarding_seed import all_seed_tmdb_ids

    url = make_url(get_settings().database_url)
    _guard_database(url.render_as_string(hide_password=False))

    records = [json.loads(line) for line in Path(args.payloads).read_text().splitlines() if line]
    tmdb = _FileTmdb(records)
    seed_ids = set(all_seed_tmdb_ids())
    seed = [
        (r["media_type"], int(r["payload"]["id"]))
        for r in records
        if r["media_type"] == "movie" and int(r["payload"]["id"]) in seed_ids
    ]
    seeded = set(seed)
    rest = [
        (r["media_type"], int(r["payload"]["id"]))
        for r in records
        if (r["media_type"], int(r["payload"]["id"])) not in seeded
    ]
    if args.limit:
        rest = rest[: args.limit]
    if args.seed_limit:
        seed = seed[: args.seed_limit]

    ctx = multiprocessing.get_context("spawn")
    round_trips = ctx.Value("q", 0)
    ready = ctx.Event()
    port = _free_port()
    proxy = ctx.Process(
        target=_run_proxy,
        args=(
            port,
            (url.host or "127.0.0.1", url.port or 5432),
            args.rtt_ms / 2000.0,
            round_trips,
            ready,
        ),
        daemon=True,
    )
    proxy.start()
    if not ready.wait(10):
        sys.exit("latency proxy did not start")

    # Same engine options as app/infrastructure/db/session.py.
    engine = create_async_engine(url.set(host="127.0.0.1", port=port), pool_pre_ping=True)
    counter: Counter[str] = Counter()

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        counter[_kind(statement)] += 1

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    results = []
    try:
        await _truncate(engine)
        wanted = set(args.scenarios.split(","))
        # `refresh` and `new` need the seed stored: write it untimed when `cold` is not asked for.
        cold = await _scenario("cold", factory, tmdb, seed, counter, round_trips, args.batch_size)
        if "cold" in wanted:
            results.append(cold)
        if "refresh" in wanted:
            results.append(
                await _scenario(
                    "refresh", factory, tmdb, seed, counter, round_trips, args.batch_size
                )
            )
        if rest and "new" in wanted:
            results.append(
                await _scenario("new", factory, tmdb, rest, counter, round_trips, args.batch_size)
            )
        if not args.keep:
            await _truncate(engine)
    finally:
        await engine.dispose()
        proxy.terminate()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--payloads", help="JSONL from --fetch")
    parser.add_argument("--fetch", metavar="OUT", help="download TMDb payloads to OUT and exit")
    parser.add_argument("--movie-pages", type=int, default=8)
    parser.add_argument("--tv-pages", type=int, default=3)
    parser.add_argument("--rtt-ms", type=float, default=150.0, help="simulated round trip")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0, help="cap titles in the `new` scenario")
    parser.add_argument(
        "--seed-limit", type=int, default=0, help="cap seed titles (cold / refresh)"
    )
    parser.add_argument("--scenarios", default="cold,refresh,new", help="comma-separated subset")
    parser.add_argument("--keep", action="store_true", help="leave the rows in place afterwards")
    parser.add_argument("--json", action="store_true", help="print one JSON object per scenario")
    args = parser.parse_args()

    if args.fetch:
        asyncio.run(
            fetch_payloads(Path(args.fetch), movie_pages=args.movie_pages, tv_pages=args.tv_pages)
        )
        return
    if not args.payloads:
        parser.error("--payloads is required (create it with --fetch)")

    for row in asyncio.run(profile(args)):
        if args.json:
            print(json.dumps(row))
            continue
        print(
            f"{row['scenario']:<8} titles={row['titles']:<4} s/title={row['s_per_title']:<6} "
            f"statements/title={row['statements_per_title']:<5} "
            f"round_trips/title={row['round_trips_per_title']:<5} "
            f"(created={row['created']} updated={row['updated']} failed={row['failed']}, "
            f"{row['seconds']} s @ rtt {args.rtt_ms:g} ms)"
        )
        for kind, n in row["by_kind_per_title"].items():
            print(f"           {n:>6}  {kind}")


if __name__ == "__main__":
    main()
