# Architecture

How CineTaste is put together and why. Product scope lives in
[PRODUCT.md](PRODUCT.md); the recommender's measured quality in
[EVALUATION.md](EVALUATION.md); signal weights in
[TASTE_SIGNALS.md](TASTE_SIGNALS.md).

## System

```
┌───────────────┐        /api/*        ┌────────────────────────┐
│  React SPA    │ ───────────────────▶ │  FastAPI (async)       │
│  Vite, TS     │  same-origin proxy   │  uvicorn               │
└───────────────┘                      └───────────┬────────────┘
   access token in memory                          │
   refresh token in httpOnly cookie                │
                                                   ▼
                         ┌─────────────────────────────────────────┐
                         │ PostgreSQL 16 + pgvector                │
                         │  titles(embedding vector(384), HNSW)    │
                         │  interaction_events (append-only)       │
                         │  taste_profiles (vector + JSONB)        │
                         └─────────────────────────────────────────┘
                         ┌─────────────────────────────────────────┐
                         │ Cache / rate-limit store                │
                         │  Redis when REDIS_URL is set,           │
                         │  otherwise in-process                   │
                         └─────────────────────────────────────────┘
                                          ▲
                              TMDb (catalog + watch providers)
```

The SPA and API share an origin in production (a Vercel rewrite forwards
`/api/*` to Render), which keeps the refresh cookie first-party — Safari blocks
third-party cookies, which would silently end sessions on reload.

## Layers

```
backend/app
├── api/            routes + Pydantic schemas. HTTP only: no business rules.
├── application/    use cases: auth, taste, recommendations, onboarding, ingest.
├── domain/         taste signal policy, errors. No framework, no I/O.
├── recommendation/ features, content vectors, profile, ranking, explanations,
│                   metrics, evaluation. Pure functions over plain objects.
└── infrastructure/ SQLAlchemy models/session, cache store, TMDb client, email.
```

Dependencies point inward. `domain/` and `recommendation/` import neither
FastAPI nor the database, which is what lets the evaluation harness run the real
ranking code with no database at all.

## Data model

| Table | Holds | Notes |
|---|---|---|
| `titles` | catalog + `embedding vector(384)` + `extra` JSONB | HNSW index (`vector_cosine_ops`), trigram indexes on names |
| `genres`, `keywords`, `people`, `credits` | normalised metadata | `credits` carries role and billing order |
| `interaction_events` | append-only log of every action | indexed by `(user_id, created_at)` |
| `user_title_state` | current state per user+title | drives feed exclusion, watchlist, history |
| `taste_profiles` | dense vector + sparse features JSONB + version | version bump invalidates cached slates |
| `recommendation_impressions` | what each slate showed | offline evaluation and future engagement metrics |
| `users`, `refresh_tokens`, `password_reset_tokens` | auth | refresh tokens hashed, rotated, with family + successor |

Events are the source of truth; the profile is derived and can be rebuilt after
any scoring change. That is why Undo is a `clear` event rather than a delete.

## Recommendation pipeline

1. **Ingest** (`application/catalog_ingest.py`) — TMDb details fetched
   concurrently in batches, each title upserted in a savepoint. For every title
   it stores a **sparse feature snapshot** and a **384-d content vector**.
2. **Signals** (`domain/taste_signals.py`) — one policy table: weight, polarity,
   tier, resulting state, feed exclusion. Per title the latest event of the
   strongest tier wins (opinion > intent > implicit), after the last `clear`.
3. **Profile** (`recommendation/profile.py`) — sparse features accumulate
   `snapshot × weight`, clamped and normalised per family so keywords can't
   drown directors; the dense vector is the weighted mean of **positively**
   rated titles. Strong positives become explanation anchors.
4. **Candidates** (`application/recommendation_service.py`) — pgvector ANN
   (cosine, `hnsw.ef_search` raised to match the requested limit) plus a
   popularity slice; cold start is popularity-ordered.
5. **Ranking** (`recommendation/pipeline.py`) —
   `0.42·cosine + 0.40·overlap − 0.12·penalty + gem + cold prior`, then MMR
   (λ=0.8), a per-genre cap of 40% of the slate, exploration slots interleaved,
   and score-ordered backfill. Vectorised with numpy and run in a worker thread.
6. **Explanations** (`recommendation/explanations.py`) — templates fed only by
   evidence that exists: shared director, shared keywords, shared tone, cited
   anchor titles.

## Request path: `GET /recommendations/for-you`

```
auth (JWT) → profile row → cached slate? ──yes──▶ hydrate titles → respond
                              │no
                              ▼
        excluded states → candidates (ANN + popular) → rank (thread)
                              │
                              ▼
        cache slate (keyed by profile version) → log impressions → respond
```

The cache key contains the profile version, so any rating invalidates it
without an explicit purge; the TTL only bounds memory. Impressions are written
once per computed slate, not per cache hit.

## Cross-cutting

- **Config** — `pydantic-settings`, one `Settings` object. `APP_ENV` is
  validated against a fixed list, and production refuses to start with a weak
  secret, localhost CORS, default database credentials, or SMTP enabled with a
  non-https public URL.
- **Cache / rate limiting** — one `KeyValueStore` interface. Redis when
  configured, in-process otherwise; a Redis failure degrades to in-process for
  30 seconds rather than failing requests.
- **Security** — bcrypt in a thread pool, short-lived access tokens in memory,
  rotating refresh cookies with reuse detection plus a short grace window for
  concurrent refreshes, per-IP rate limits, security headers, strict CORS.
- **Errors** — every failure is `{code, message, request_id}`; validation adds
  `errors[]`.
- **Observability** — request-id middleware, structured access logs, optional
  Sentry on both sides. httpx request logging is capped at WARNING because TMDb
  v3 puts the API key in the query string.

## Performance notes

| Concern | Approach |
|---|---|
| Slate latency | Cached per profile version; ranking vectorised with numpy and moved off the event loop |
| Candidate generation | ANN via HNSW with `ef_search` raised so filtering doesn't silently shrink the pool |
| N+1 queries | Relationships never load implicitly (`lazy="raise"`); each query states what it needs |
| Password hashing | bcrypt in a worker thread |
| Profile recompute | Small indexed event scan + a column-only title fetch; O(interactions) per rating |
| Free-tier Redis | Optional by design: one process uses an in-process store |

## Frontend

- React 19 + TypeScript, Vite, React Router, lazy-loaded authenticated routes.
- `AuthContext` keeps the access token in memory only; session restore and 401
  retries share one single-flight refresh, so two tabs can't trip reuse detection.
- The rating scale is defined once (`features/taste/ratingScale.ts`) and used by
  For You, onboarding, the detail page and the undo toast.
- Accessibility: skip link, live regions for loading/empty/error, keyboard
  shortcuts, reduced-motion and forced-colors support, axe checks in CI.

## Testing and CI

Unit tests (pure logic, no I/O) → integration tests (real Postgres, schema built
by Alembic, fail rather than skip when the database is missing) → Playwright e2e
with axe. CI additionally builds the image and boots the container. See
[TESTING.md](TESTING.md).
