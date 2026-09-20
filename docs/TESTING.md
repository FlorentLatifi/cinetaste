# Testing

Three layers, each answering a different question.

| Layer | Count | Question | Needs |
|---|---|---|---|
| Unit (`backend/tests/*.py`) | 159 | Is the logic right? | nothing |
| Integration (`backend/tests/integration/`) | 15 | Do the API, migrations and database agree? | Postgres + pgvector |
| End-to-end (`frontend/e2e/`) | 34 | Does the app work in a browser, accessibly? | built SPA |

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
