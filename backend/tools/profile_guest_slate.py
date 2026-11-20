"""Profile the guest slate path against a LOCAL database. Never run this at production.

    # one-time: a throwaway database (docker compose: the "db" service)
    createdb cinetaste_bench && psql cinetaste_bench -c "CREATE EXTENSION vector"
    export DATABASE_URL=postgresql+asyncpg://cinetaste:cinetaste@localhost:5432/cinetaste_bench
    python -m alembic upgrade head

    python tools/profile_guest_slate.py --seed 4700     # synthetic catalog, same size as prod
    python tools/profile_guest_slate.py --runs 40       # timings + per-statement cost

``python -m app.scripts.seed_demo_catalog`` only creates 36 titles, which never fills the
~370-candidate pool (250 nearest + 120 popular) the production path ranks, so the cost is
invisible there. ``--seed`` bulk-inserts N synthetic titles built with the real embedding
code, with realistic row width (overview text, backdrop, keyword/people features).

Safety: refuses any DATABASE_URL whose host is not loopback / ``db`` and whose database name
does not end in ``_bench`` or ``_test``. Seeding refuses to touch a non-empty catalog it did
not create.

What is measured (in-process, no network, so these are server-side costs only):

* every phase of ``guest_slate`` (existence check, profile titles, ANN query, popularity
  query, relationship loading, ranking thread, response serialisation),
* every SQL statement (count, wall time, rows, bytes of result when known).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event, func, insert, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family",
    "Fantasy", "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction",
    "Thriller", "War", "Western", "TV Movie",
]  # fmt: skip
WORDS = (
    "a detective hunts ghost rain port city astronaut isolation failing station chef rival "
    "family restaurant empire cartographer maps kingdom ruler learns cost mercy radio signal "
    "grows flowers whisper secrets courier uncovers convoy conspiracy organizers fall love "
    "strike composer rebuilds shattered ensemble surveyor redraws dying frontier hackers "
    "weaponize fossilized code deep past coastal town hides generation secret network outage "
    "reveals parallel cities siblings inherit chaotic food truck gardeners tend living metal"
).split()
KEYWORDS = [f"kw{i:03d}" for i in range(180)] + [
    "detective", "neo-noir", "feel-good", "mind-bending", "heist", "time travel", "dystopia",
    "coming of age", "based on novel", "revenge", "road trip", "found family", "survival",
]  # fmt: skip
PEOPLE = [f"Person {i:04d}" for i in range(600)]
SEED_MARK = "bench-synthetic"


def _guard_database() -> str:
    from app.core.config import get_settings

    url = make_url(get_settings().database_url)
    host = (url.host or "").lower()
    name = url.database or ""
    if host not in {"localhost", "127.0.0.1", "::1", "db"}:
        sys.exit(f"refusing: host {host!r} is not local. This tool is for a local stack only.")
    if not name.endswith(("_bench", "_test")):
        sys.exit(
            f"refusing: database {name!r} must end in _bench or _test "
            "(createdb cinetaste_bench; this tool inserts thousands of rows)."
        )
    return name


async def seed(count: int) -> None:
    from app.infrastructure.db.models.catalog import Genre, Title, TitleGenre
    from app.infrastructure.db.session import async_session_factory
    from app.recommendation.embeddings import PersonSignal, build_title_signals

    rng = random.Random(7)
    async with async_session_factory() as session:
        existing = int(await session.scalar(select(func.count()).select_from(Title)) or 0)
        if existing:
            marked = int(
                await session.scalar(
                    select(func.count()).select_from(Title).where(Title.extra["seed"].astext == SEED_MARK)
                )
                or 0
            )
            if marked != existing:
                sys.exit(f"refusing to seed: catalog has {existing} titles, {marked} are ours.")
            if existing >= count:
                print(f"catalog already has {existing} synthetic titles; nothing to do")
                return

        genre_ids: dict[str, object] = {}
        for i, name in enumerate(GENRES):
            gid = uuid4()
            session.add(Genre(id=gid, name=name, external_tmdb_id=7000 + i))
            genre_ids[name] = gid
        await session.flush()

        title_rows: list[dict] = []
        link_rows: list[dict] = []
        for i in range(count):
            genres = rng.sample(GENRES, rng.choice([1, 2, 2, 3]))
            media = "tv" if rng.random() < 0.18 else "movie"
            year = rng.randint(1950, 2025)
            runtime = rng.randint(22, 60) if media == "tv" else rng.randint(78, 190)
            name = f"{rng.choice(WORDS).title()} {rng.choice(WORDS).title()} {i}"
            overview = " ".join(rng.choices(WORDS, k=rng.randint(40, 70))).capitalize() + "."
            keywords = rng.sample(KEYWORDS, rng.randint(6, 16))
            people = [PersonSignal(name=rng.choice(PEOPLE), role="director")] + [
                PersonSignal(name=rng.choice(PEOPLE), role="cast") for _ in range(6)
            ]
            emb, _feat, meta = build_title_signals(
                name=name, overview=overview, genres=genres, keywords=keywords, people=people,
                media_type=media, release_year=year, runtime=runtime, original_language="en",
                countries=["US"],
            )  # fmt: skip
            meta["seed"] = SEED_MARK
            tid = uuid4()
            # Popularity is heavy-tailed like TMDb's: a few huge, a long flat tail.
            popularity = round(rng.paretovariate(1.2) * 4, 3)
            title_rows.append(
                dict(
                    id=tid, media_type=media, name=name, original_name=name, overview=overview,
                    release_date=date(year, rng.randint(1, 12), 1), runtime=runtime,
                    popularity=popularity, vote_average=round(rng.uniform(4.5, 8.8), 1),
                    vote_count=rng.randint(30, 30000),
                    poster_path=f"/{uuid4().hex[:27]}.jpg", backdrop_path=f"/{uuid4().hex[:27]}.jpg",
                    original_language="en", external_tmdb_id=1_000_000 + existing + i,
                    embedding=emb, extra=meta,
                )
            )  # fmt: skip
            link_rows.extend({"title_id": tid, "genre_id": genre_ids[g]} for g in genres)
        for start in range(0, len(title_rows), 500):
            await session.execute(insert(Title), title_rows[start : start + 500])
        await session.execute(insert(TitleGenre), link_rows)
        await session.commit()
    print(f"seeded {count} synthetic titles")


# --------------------------------------------------------------------------- measurement


class Phases:
    """Wall-clock per named phase, accumulated per run."""

    def __init__(self) -> None:
        self.runs: list[dict[str, float]] = []
        self.current: dict[str, float] = defaultdict(float)

    def start_run(self) -> None:
        self.current = defaultdict(float)

    def end_run(self) -> None:
        self.runs.append(dict(self.current))

    def wrap(self, owner, attr: str, label: str):  # noqa: ANN001, ANN201
        original = getattr(owner, attr)

        async def timed(*args, **kwargs):  # noqa: ANN002, ANN003
            t0 = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                self.current[label] += (time.perf_counter() - t0) * 1000

        setattr(owner, attr, timed)
        return original


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


async def pick_reactions(rng: random.Random) -> list[tuple]:
    from app.infrastructure.db.models.catalog import Title
    from app.infrastructure.db.session import async_session_factory

    async with async_session_factory() as session:
        ids = list(
            (await session.scalars(select(Title.id).order_by(Title.popularity.desc()).limit(400))).all()
        )
    picks = rng.sample(ids, 8)
    actions = ["rate_4", "rate_4", "rate_3", "rate_1", "rate_4", "rate_2", "haven't_seen", "rate_3"]
    return [(str(t), a) for t, a in zip(picks, actions, strict=True)]


async def profile(runs: int, limit: int, label: str, use_pool: bool) -> dict:
    from app.api.schemas.titles import RecommendationItemOut
    from app.application import recommendation_service as rs
    from app.application.onboarding_service import count_ratings, normalize_reactions
    from app.application.recommendation_service import RecommendationService
    from app.core.config import get_settings
    from app.infrastructure.db.session import async_session_factory, engine

    settings = get_settings()
    pool = None
    if use_pool:
        from app.application.popular_pool import get_popular_pool  # absent before this change

        pool = get_popular_pool(settings.guest_popular_pool_ttl_seconds)
    reactions = await pick_reactions(random.Random(11))
    normalized = normalize_reactions([{"title_id": t, "action": a} for t, a in reactions])
    print(f"[{label}] ratings/positives:", count_ratings(normalized))

    phases = Phases()
    stmts: dict[str, list[float]] = defaultdict(list)
    stmt_rows: dict[str, int] = defaultdict(int)
    started: dict[int, float] = {}

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        started[id(cursor)] = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        t0 = started.pop(id(cursor), None)
        if t0 is None:
            return
        head = " ".join(statement.split())[:110]
        stmts[head].append((time.perf_counter() - t0) * 1000)
        try:
            stmt_rows[head] += max(cursor.rowcount, 0)
        except Exception:  # noqa: BLE001
            pass

    from anyio import to_thread

    original_run_sync = to_thread.run_sync

    async def timed_run_sync(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        t0 = time.perf_counter()
        try:
            return await original_run_sync(func, *args, **kwargs)
        finally:
            phases.current["rank_titles (thread)"] += (time.perf_counter() - t0) * 1000

    rs.to_thread.run_sync = timed_run_sync  # type: ignore[assignment]
    phases.wrap(RecommendationService, "_load_candidates", "_load_candidates")
    phases.wrap(RecommendationService, "_rank_for_profile", "_rank_for_profile")
    phases.wrap(RecommendationService, "existing_title_ids", "existing_title_ids")
    phases.wrap(RecommendationService, "guest_slate", "guest_slate (total)")
    original_ltp = rs.load_profile_titles

    async def timed_ltp(*a, **k):  # noqa: ANN002, ANN003, ANN202
        t0 = time.perf_counter()
        try:
            return await original_ltp(*a, **k)
        finally:
            phases.current["load_profile_titles"] += (time.perf_counter() - t0) * 1000

    rs.load_profile_titles = timed_ltp  # type: ignore[assignment]

    async def one_run() -> int:
        phases.start_run()
        t0 = time.perf_counter()
        async with async_session_factory() as session:
            service = RecommendationService(session, settings, **({'popular_pool': pool} if pool else {}))
            known = await service.existing_title_ids([t for t, _e in normalized])
            kept = [(t, e) for t, e in normalized if t in known]
            ranked = await service.guest_slate(kept, limit=limit)
            s0 = time.perf_counter()
            body = [RecommendationItemOut.from_ranked(t, i) for t, i in ranked]
            payload = json.dumps([b.model_dump(mode="json") for b in body])
            phases.current["serialise response"] += (time.perf_counter() - s0) * 1000
        phases.current["TOTAL (route body)"] = (time.perf_counter() - t0) * 1000
        phases.end_run()
        return len(payload)

    for _ in range(3):  # warm pool, plan cache, numpy
        await one_run()
    phases.runs.clear()
    stmts.clear()
    stmt_rows.clear()
    size = 0
    for _ in range(runs):
        size = await one_run()

    report: dict = {"label": label, "runs": runs, "limit": limit, "response_bytes": size, "phases_ms": {}}
    names = sorted({k for r in phases.runs for k in r})
    for name in names:
        vals = [r.get(name, 0.0) for r in phases.runs]
        report["phases_ms"][name] = {
            "p50": round(statistics.median(vals), 1),
            "p95": round(_pct(vals, 0.95), 1),
        }
    report["statements"] = [
        {
            "sql": head,
            "calls_per_run": round(len(v) / runs, 2),
            "p50_ms": round(statistics.median(v), 1),
            "rows_per_run": round(stmt_rows[head] / runs, 1),
        }
        for head, v in sorted(stmts.items(), key=lambda kv: -sum(kv[1]))
    ]
    report["statements_per_run"] = round(sum(len(v) for v in stmts.values()) / runs, 1)
    await engine.dispose()
    return report


async def profile_http(runs: int, limit: int, label: str) -> dict:
    """The real route, in-process (no socket): unique answers vs the same answers again."""
    from httpx import ASGITransport, AsyncClient

    from app.infrastructure.cache import reset_cache_for_tests
    from app.infrastructure.db.session import engine
    from app.main import app

    rng = random.Random(23)
    bodies = []
    for _ in range(runs):
        reactions = await pick_reactions(rng)
        bodies.append({"reactions": [{"title_id": t, "action": a} for t, a in reactions], "limit": limit})
    url = "/api/v1/guest/recommendations"

    async def timed(client, body) -> tuple[float, dict]:  # noqa: ANN001
        t0 = time.perf_counter()
        res = await client.post(url, json=body)
        ms = (time.perf_counter() - t0) * 1000
        assert res.status_code == 200, res.text
        return ms, res.json()

    out: dict = {"label": label, "runs": runs, "limit": limit}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
        await reset_cache_for_tests()
        for body in bodies[:3]:  # warm pool/plan caches with other answers
            await timed(client, body)
        await reset_cache_for_tests()
        misses = [(await timed(client, b))[0] for b in bodies]  # every body is new: all misses
        same = bodies[0]
        first_ms, first_json = await timed(client, same)  # may already be cached from the pass above
        repeats, identical = [], True
        for _ in range(runs):
            ms, body_json = await timed(client, same)
            repeats.append(ms)
            identical &= body_json == first_json
    out["unique_answers_ms"] = {"p50": round(statistics.median(misses), 1), "p95": round(_pct(misses, 0.95), 1)}
    out["repeated_answers_ms"] = {"p50": round(statistics.median(repeats), 1), "p95": round(_pct(repeats, 0.95), 1)}
    out["repeat_bodies_identical"] = identical
    await engine.dispose()
    return out


def _print(report: dict) -> None:
    print(f"\n== {report['label']} (LOCAL, {report['runs']} runs, limit={report['limit']}) ==")
    print(f"response body: {report['response_bytes']} bytes, SQL statements/run: {report['statements_per_run']}")
    print(f"{'phase':32} {'p50 ms':>8} {'p95 ms':>8}")
    for name, v in report["phases_ms"].items():
        print(f"{name:32} {v['p50']:8.1f} {v['p95']:8.1f}")
    print("\nstatements (by total time):")
    for s in report["statements"][:8]:
        print(f"  {s['calls_per_run']:>5}x {s['p50_ms']:>7.1f} ms {s['rows_per_run']:>7.0f} rows  {s['sql']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--seed", type=int, default=0, help="seed N synthetic titles, then exit")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--label", default="run")
    parser.add_argument("--pool", action="store_true", help="share the popular-candidate pool (after the fix)")
    parser.add_argument("--http", action="store_true", help="measure the route in-process: unique vs repeated answers")
    parser.add_argument("--json", help="also write the report to this path")
    args = parser.parse_args()

    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
    db_name = _guard_database()
    print(f"database: {db_name} (local)")
    if args.seed:
        await seed(args.seed)
        return
    if args.http:
        report = await profile_http(args.runs, args.limit, args.label)
        print(json.dumps(report, indent=2))
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=2))
        return
    report = await profile(args.runs, args.limit, args.label, args.pool)
    _print(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
