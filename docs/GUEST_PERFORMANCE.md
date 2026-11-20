# Guest slate performance and abuse limits

Scope: `GET /guest/cards` and `POST /guest/recommendations` (public, no account).
This documents what was measured, what changed, and what it does **not** prove.

> **Every number below is LOCAL**: a development container (4 vCPU, Locust on the same
> box), one uvicorn worker, no Redis, Postgres 16 + pgvector 0.6 on loopback with TLS
> off (like the compose DB), and a **synthetic** 4,700-title catalog (the documented
> production size) built with the real embedding code. Nothing was run against Render,
> Vercel or any hosted database. **They do not explain, and must not be read as a fix
> for, the 5.9 s / 3.8 s measured externally** (see "What this does not explain").

## What changed

| # | Change | Where |
|---|---|---|
| 1 | Process-wide concurrency cap on guest ranking: bounded wait, then `503 guest_busy` + `Retry-After` | `app/core/concurrency.py`, `routes/guest.py` |
| 2 | Cache of slates by hash of normalized answers + limit (TTL 300 s, 0 = off) via the existing store; no Redis needed | `app/application/guest_slates.py` |
| 3 | One shared, per-process copy of the *popularity* half of the candidate pool (same rows for every guest) | `app/application/popular_pool.py` |
| 4 | Local profiler and Locust load test (refuse non-local targets, no override) | `backend/tools/` |

Ranking is unchanged: 60 random answer sets (1,200 ranked items: ids, scores, reasons,
genres) are byte-identical between the original code and the new code with the pool
enabled. Auth is untouched. No new infrastructure.

## Profile (before any change, local)

Per request, p50, 4,700 titles, 12 results: **~66-70 ms** total.

| Phase | ms | Share |
|---|---|---|
| `_load_candidates` | 43-48 | ~65 % |
| `rank_titles` (thread) | 15 | ~22 % |
| existence check, profile titles, serialisation | ~4 | ~6 % |

Inside `_load_candidates` the database's own work is small (HNSW search 6 ms, popularity
sequential scan 2.5 ms); the rest is hydrating ~378 rows: ~10 ms decoding 385 vectors
from pgvector's text form, ORM instance building, and two `selectinload(genres)`
statements.

Tried and **rejected**, with data:

| Idea | Result |
|---|---|
| Load only the columns ranking needs (`load_only`) | -2 ms; breaks serialisation of the slate (deferred columns in async) |
| Core rows instead of ORM entities | -6 ms but ripples through For You and its tests |
| `embedding::real[]` instead of text | **slower** (35.7 vs 22.6 ms) |
| numpy `fromstring` for vector decode | **slower** (44 vs 25 µs/vector) |
| pgvector binary codec | would be faster, but float32-widened values differ in the last digits from today's `float(text)`; near-ties could flip. Breaks "don't change ranking" |
| `joinedload(genres)` | multiplies vector bytes by genres-per-title |

Taken: the popularity slice is identical for every request, so it is now read once per
TTL instead of per request (2 of 9 SQL statements, ~1/3 of candidate rows).

## Results (local)

Service level, `guest_slate` miss path, median of 3 interleaved pairs x 50 runs:

| | before | after |
|---|---|---|
| total p50 / p95 | 70.9 / 93.7 ms | **50.1 / 61.9 ms** (-29 % / -34 %) |
| `_load_candidates` p50 | 47.8 ms | 29.2 ms |
| SQL statements per request | 9 | 7 |

Route level (`POST /guest/recommendations`, in-process), median of 5 interleaved pairs x 40:

| | before p50 / p95 | after p50 / p95 |
|---|---|---|
| unique answers (miss) | 69.7 / 90.7 ms | **51.5 / 67.1 ms** |
| same answers again | 70.3 / 88.4 ms | **6.8 / 8.1 ms** (response bodies identical) |

A hit still reads the titles back (2 statements) rather than caching whole responses:
the stored payload is ids, scores and reasons (a few hundred bytes per item) so the shared
in-memory store cannot be filled with poster/overview data.

Closed-loop load, one worker, Locust on the same machine, 30 s, every visitor sends
*different* answers (worst case), p50 / p95 of **served** slates:

| | served/s | p50 | p95 | shed |
|---|---|---|---|---|
| 8 users, original | 11.9 | 610 ms | 830 ms | n/a |
| 8 users, new | 16.7 | 440 ms | 570 ms | 0 % |
| 40 users, original | 10.6 | 3,300 ms | 5,000 ms | n/a |
| 40 users, new, limit 2, wait 0.5 s | 13.9 | 580 ms | 720 ms | 48 % |
| 40 users, new, limit 4, wait 0.5 s | 14.0 | 790 ms | 940 ms | 41 % |
| 40 users, new, limit 8, wait 0.5 s | 14.2 | 1,300 ms | 1,600 ms | 28 % |

Reading it: one process is **CPU-bound at ~14 slates/s** whatever the cap, so the cap
does not add capacity; it decides who waits. Without it, 40 visitors all wait ~3-5 s. With
it, those admitted stay near the 800 ms target and the rest get a fast `503` they can retry.
Defaults are `GUEST_RANK_MAX_CONCURRENT=2`, `GUEST_RANK_MAX_WAIT_SECONDS=1.0` (the
sweep used 0.5 s; 1.0 s is deliberately more patient for real visitors on slower hardware).

## Limits and open items

- **What this does not explain.** Locally the whole path is ~50-70 ms; the externally
  measured 5.9 s / 3.8 s cannot come from code that costs that on a 4-core box. Candidates,
  **not verified**: database latency per round trip (7-9 statements + connection checkout
  and pre-ping per request), a cold or suspended hosted database, free-tier CPU, TLS to the
  DB. Cheapest next step with no load: log phase timings for one real request. Do not
  load-test production to find out. A fix on that list is an infrastructure change
  (region, plan, database): **out of scope here, per the brief.**
- **The cap is per process.** With N workers the ceiling is N x limit. Production runs one.
- **The cap bounds cost, not request count.** A forged-`X-Forwarded-For` flood still gets
  through the per-IP limiter; it now meets the cap instead of the database. Cards are not
  capped.
- **`GET /guest/cards` cold misses are slow under bursts** (2.3 s p50 for 40 simultaneous
  uncached requests locally). Cached after the first; not addressed here. A single-flight
  on the cache miss is the obvious follow-up.
- **No single-flight for slates.** Two identical requests arriving together both rank
  (once, then it is cached). Rare in practice, cheap to add if it is not.
- **Slate cache keys keep answer order.** Order is the event order the profile builder
  sees and was not proven irrelevant, so reordered answers are a different entry. Lower hit
  rate, never a wrong answer.
- **Shared cache memory.** Slates share the in-process store's 20,000-entry LRU with For You
  slates and the deck. Someone sending thousands of distinct answer sets can evict other
  entries (slower, not wrong), at a few hundred bytes to a few KB each.
- **Popular pool freshness.** A newly ingested title can join the popularity half of the
  candidate pool up to `GUEST_POPULAR_POOL_TTL_SECONDS` late. The ANN half is live.
- **Defaults are tuned on a dev box.** Re-sweep limit/wait on hardware like production's.
- Possible schema improvement (not done; ~2.5 ms at 4,700 titles, linear in catalog size):
  a partial index on `titles (popularity DESC) WHERE embedding IS NOT NULL`, since that
  query is currently a sequential scan plus top-N sort.

## Reproduce (local only)

```bash
createdb cinetaste_bench && psql cinetaste_bench -c "CREATE EXTENSION vector"
export DATABASE_URL=postgresql+asyncpg://cinetaste:cinetaste@localhost:5432/cinetaste_bench
alembic upgrade head
python tools/profile_guest_slate.py --seed 4700
python tools/profile_guest_slate.py --runs 50 --pool      # service level (omit --pool for the old path)
python tools/profile_guest_slate.py --http --runs 40      # route level: unique vs repeated answers

pip install locust                                        # optional, not in requirements
RATE_LIMIT_ENABLED=false REDIS_URL= uvicorn app.main:app --port 8000 &
LOADTEST_THINK_MIN=0 LOADTEST_THINK_MAX=0.05 \
  locust -f tools/loadtest_guest.py NewVisitor --host http://localhost:8000 --headless -u 40 -r 40 -t 30s
```

`docker compose` works the same (`python -m app.scripts.seed_demo_catalog` creates only 36
titles, too few to fill the ~370-candidate pool, hence `--seed`). The profiler and load
test refuse non-loopback / non-private hosts and non-`_bench`/`_test` databases; there is
no override.
