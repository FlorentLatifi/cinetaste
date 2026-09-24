# Testing

Three layers, each answering a different question.

| Layer | Count | Question | Needs |
|---|---|---|---|
| Unit — backend (`backend/tests/*.py`) | 226 | Is the logic right? | nothing |
| Unit — frontend (`frontend/src/**/*.test.ts`) | 68 | Do the pure functions hold? | nothing |
| Integration (`backend/tests/integration/`) | 20 | Do the API, migrations and database agree? | Postgres + pgvector |
| End-to-end (`frontend/e2e/`) | 40 | Does the app work in a browser, accessibly? | built SPA |

## Coverage

Backend 84%, gated in CI two ways: `--cov-fail-under=80` on the total, and
`backend/tools/coverage_gate.py` on each package, because a total is easy to
hold up with well-covered trivia while ranking and taste quietly rot.

One setting makes those numbers mean anything. SQLAlchemy's async layer runs
database work inside greenlets, and coverage does not follow a greenlet switch
unless `concurrency` says so — without it the report called the whole body of
the interactions endpoint dead code while the test driving it passed. It is set
in `backend/pyproject.toml`; do not remove it.

Frontend unit coverage is scoped to the pure-logic modules (`vite.config.ts`),
since components and pages are the e2e suite's job and blending the two would
make one number mean neither.

## The test database

The integration suite TRUNCATEs every table, so it must never point at the
database the dev server uses. `tests/conftest.py` rewrites `DATABASE_URL` to
`<name>_test` before anything imports the app, and the integration fixtures
refuse outright to run against a name that does not end in `_test`.
`docker compose up db` creates `cinetaste_test` alongside the dev database on a
fresh volume; if your volume predates that, create it by hand:

```bash
docker compose exec db psql -U cinetaste -d postgres -c "CREATE DATABASE cinetaste_test OWNER cinetaste"
docker compose exec db psql -U cinetaste -d cinetaste_test -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

## Running

```bash
# Unit — pure functions, mocked sessions
cd backend && pytest -m "not integration"

# Integration — real Postgres (docker compose up -d db)
export DATABASE_URL=postgresql+asyncpg://cinetaste:cinetaste@localhost:5432/cinetaste
INTEGRATION_REQUIRED=1 pytest -m integration

# End-to-end + accessibility
cd frontend && npm run build && npx playwright test

# Offline recommender evaluation (not a pass/fail gate)
cd backend && python -m app.scripts.evaluate_recommender
```

## Conventions that matter

**Integration tests build the schema with `alembic upgrade head`.** Migrations
are part of what's being tested; `create_all` would hide a broken migration.

**They fail instead of skipping.** Locally a missing database skips them so unit
runs stay green; in CI `INTEGRATION_REQUIRED=1` turns that into a failure. This
matters: they previously skipped silently because the module-level engine was
bound to the first test's event loop, so three of five never ran. They now share
one loop.

**CI runs the integration suite twice** — without Redis (as production runs) and
with it — because the cache and rate limiter have two backends.

**Upgrades are verified, not assumed.** `test_session_commit_timing.py` pins
that a failed commit reaches the client as an error: FastAPI >= 0.118 runs a
yield dependency's exit code after the response is sent, which would turn a
lost write into a 200. The session dependency uses `scope="function"` for that
reason, and the test fails if it is dropped.

**Tests assert behaviour, not implementation.** `test_recommender_invariants.py`
pins the product's promises (a later dislike overrides an earlier like, a slate
isn't one genre, explanations only cite real evidence). Several of those
reproduce bugs found in the audit and fail on the old code.

**The evaluation harness runs production code.** `evaluate` builds profiles with
the same `effective_title_signals` → `build_profile` → `rank_titles` path the API
uses; a re-implementation would measure the re-implementation.

## Where things are covered

| Area | Where |
|---|---|
| Signal policy, tiers, decay, undo | `test_taste_signals.py`, `test_recommender_invariants.py` |
| Profile building, ranking, diversity, explanations | `test_recommender_invariants.py`, `test_recommendation_pipeline.py`, `test_explanations.py` |
| Features and content vectors | `test_embeddings_and_rank.py` |
| Metrics and evaluation | `test_metrics_and_evaluation.py` |
| Auth, reset tokens, refresh families | `test_auth_account.py`, `test_refresh_families.py`, `test_security.py` |
| Config safety, cookies, rate-limit IPs, cache store | `test_config_production.py`, `test_cookies.py`, `test_middleware_and_ready.py` |
| TMDb retries | `test_tmdb_client.py` |
| Every API endpoint | `integration/test_api_endpoints.py` |
| Ingestion (failures, TV creators, idempotency) | `integration/test_catalog_ingest.py` |
| Full user journey | `integration/test_api_flow.py`, `frontend/e2e/interactions.spec.ts` |

## Gaps worth knowing

- Coverage is reported in CI but not gated; pick a floor from a measured run.
- No frontend unit tests (hooks are covered indirectly through e2e).
- No load testing; latency claims come from micro-benchmarks, not a load run.
