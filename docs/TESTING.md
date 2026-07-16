# Testing strategy — recommendations & taste

Focus: **fast, deterministic unit tests** for the ranking brain. Integration tests with Postgres/Redis come later when CI has services.

## Layers

| Layer | What we test | How | Speed |
|-------|----------------|-----|-------|
| **A. Signal policy** | Weights, zero-signal, feed exclusion | Pure unit (`taste_signals`) | Instant |
| **B. Features / embeddings** | Sparse schema, director/cast weights, tones | Pure unit | Instant |
| **C. Pipeline** | Score blend, cold start, MMR, exclude_ids, reasons | Pure unit + fake titles | Instant |
| **D. Explanations** | Anchors, human copy, memory strip | Pure unit | Instant |
| **E. Onboarding complete** | Gates, action mapping, record calls | Service + mocks (no DB) | Instant |
| **F. Seed deck** | Primary size, diversity axes | Pure unit | Instant |
| **G. API / DB** *(later)* | `/onboarding/complete`, `/for-you` E2E | pytest + postgres + redis | Slow |

**Critical path first (this repo today):** A–F.  
**Explicitly deferred:** full HTTP E2E, Redis slate cache, live TMDb.

## Critical scenarios

### 1. User action signals
- Positive (`rate_3`, `rate_4`, `like`) increase matching sparse score.
- Negative (`rate_1`, `not_interested`) apply penalty channel.
- **`haven't_seen` never contributes** (`affects_taste` false, weight 0).
- Watchlist is mild positive, weaker than Good.

### 2. Cold start
- Empty / weak profile still produces a slate.
- Popularity prior engages when vector/features are thin.
- Onboarding seed deck loads without relying on pure popularity order.

### 3. MMR diversity
- Near-duplicate high-score titles lose to a diverse lower-score title at mid λ.
- Genre soft-cap path still returns `slate_size` items when pool is large enough.

### 4. Explanations
- Strong favorites + shared director → `because_you_liked` citing title names.
- Memory key stripped from scoring features.
- Reasons always non-empty with human-readable `message`.

### 5. Onboarding completion
- Rejects &lt; 6 real ratings (haven’t-seen does not count).
- Rejects &lt; 2 positive ratings.
- Accepts 6+ ratings with 2+ positives; maps legacy like/dislike.
- Calls `record_interaction` once per valid reaction; sets `onboarding_completed_at`.

## How to run

```powershell
cd backend
$env:JWT_SECRET = "test-secret-for-unit-tests-only-32chars"
$env:DATABASE_URL = "postgresql+asyncpg://u:p@localhost:5432/t"
python -m pytest tests/ -q
```

`DATABASE_URL` / `JWT_SECRET` are required because some modules import app settings at import time (even for pure unit modules that transitively load infrastructure). Prefer fixing that import side-effect over time.

## File map

| File | Covers |
|------|--------|
| `tests/test_taste_signals.py` | Policy table |
| `tests/test_embeddings_and_rank.py` | Features, sparse, MMR smoke, rank slate |
| `tests/test_explanations.py` | Human reasons |
| `tests/test_onboarding_seed.py` | Curated deck |
| `tests/test_recommendation_pipeline.py` | Cold start, signals → rank, MMR, exclude |
| `tests/test_onboarding_complete.py` | Complete gates + mocks |
| `tests/conftest.py` | Shared fakes |

## Adding tests

1. Prefer **pure functions** (`rank_titles`, `mmr_select`, `build_reasons`, `get_policy`).
2. Avoid DB unless the bug is in SQLAlchemy/query wiring.
3. Use `conftest` title fakes (`FakeTitle`) so embeddings/features stay consistent.
4. Keep each test &lt; ~30 lines; one behavior per test name.
