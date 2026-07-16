# System Architecture

## Design goals

1. **Taste profile is the core domain** — catalog is supporting infrastructure.
2. **Explainability is first-class** — reasons flow through the ranking pipeline, not bolted on in the UI.
3. **Simple now, extensible later** — onion/clean layering + strategy pattern for rankers; no CQRS/event-sourcing until measured need.
4. **Production-shaped from day one** — Docker, health checks, migrations, secrets via env, structured logs.
5. **Fast enough to feel magical** — Redis for slates and hot catalog; pgvector for similarity; async I/O.

---

## High-level topology

```
                    ┌─────────────┐
                    │   Vercel    │
                    │  React SPA  │
                    └──────┬──────┘
                           │ HTTPS / JWT
                    ┌──────▼──────┐
                    │   FastAPI   │  (Railway / Render)
                    │  API + DI   │
                    └──┬─────┬────┘
           ┌───────────┘     └───────────┐
           ▼                             ▼
    ┌─────────────┐               ┌─────────────┐
    │ PostgreSQL  │               │    Redis    │
    │ + pgvector  │               │ cache/rate  │
    └─────────────┘               └─────────────┘
           ▲
           │ batch ingest / embed
    ┌──────┴──────┐
    │  Workers    │  (same image, separate process later)
    │  TMDb sync  │
    └─────────────┘
```

**MVP simplification:** one API process; background jobs as CLI commands or lightweight async tasks. Split workers when job volume justifies it.

---

## Backend layering (onion / clean)

```
backend/
  app/
    main.py                 # composition root, middleware
    api/                    # HTTP adapters (routers, schemas)
    application/            # use cases / services
    domain/                 # entities, value objects, ports (interfaces)
    infrastructure/         # SQLAlchemy, Redis, TMDb client, security
    recommendation/         # rankers, diversifiers, explainers (strategies)
```

### Dependency rule

* `domain` has **no** framework imports.
* `application` depends on domain ports only.
* `infrastructure` implements ports (repositories, cache, external APIs).
* `api` depends on application services via DI.
* `recommendation` is a domain/application module with swappable **Strategy** implementations.

### Patterns we use (and why)

| Pattern | Use | Justification |
|---------|-----|----------------|
| Repository | Persist users, titles, signals, profiles | Testability, swap storage |
| Strategy | Ranking, diversification, explanation | Multiple rec policies without if-soup |
| DI | FastAPI `Depends` + factory | Clean tests, single composition root |
| CQRS / Events | **Not MVP** | Add when write/read load or async pipelines demand it |

---

## Core domains

### 1. Identity
* User, credentials, refresh tokens, sessions
* Auth: access JWT (short) + refresh token (rotating, hashed at rest)

### 2. Catalog
* Title (movie/TV), genres, people, credits, keywords
* External IDs (TMDb) for ingest — **never expose as product core**
* Content embedding vector (pgvector) derived from structured features + text

### 3. Signals
* Explicit: like, dislike, watchlist, not_interested
* Implicit (schema-ready): skip, dwell, detail_view (collect when UI supports)
* Immutable-ish event log + aggregated counters (keep both paths simple)

### 4. Taste profile
* Per-user weighted feature vector + interpretable feature weights
* Versioned: recompute on signal change (async or sync for MVP)
* Stored as: dense vector (for ANN) + sparse feature map (for explanations)
* **Signal policy (weights, zero-signal, feed exclusion):** [`docs/TASTE_SIGNALS.md`](TASTE_SIGNALS.md) · code: `app/domain/taste_signals.py`

### 5. Recommendations
* Candidate generation → score → diversify → explain → cache slate
* Output: ordered items + reason codes + debug scores (debug only in non-prod)

---

## Recommendation pipeline

```
┌──────────────┐    ┌─────────────────┐    ┌──────────────┐
│  Candidates  │ →  │  Score / Rank   │ →  │ Diversify    │
│  (ANN+rules) │    │  (taste match)  │    │ (MMR / caps) │
└──────────────┘    └─────────────────┘    └──────┬───────┘
                                                  │
                     ┌─────────────────┐    ┌─────▼───────┐
                     │  Cache slate    │ ←  │  Explain    │
                     │  (Redis)        │    │  (reasons)  │
                     └─────────────────┘    └─────────────┘
```

### Stage details (MVP)

**1. Candidates**
* ANN: nearest titles to user taste vector (pgvector)
* Filters: not already disliked / not_interested; optional language; media type
* Exploration pool: slightly farther neighbors or “high quality / lower popularity”
* Hard excludes: items already strongly negative

**2. Score**
* Base: cosine(user_vector, title_vector)
* Feature boosts: matching top genres, favorite people, preferred runtime/year band
* Soft penalties: over-represented franchise in recent history
* Keep formula **documented and unit-tested**

**3. Diversify**
* MMR (λ tunable) on embedding space
* Soft caps: max N per primary genre per slate; franchise de-dupe
* Reserve K slots for exploration / hidden gems

**4. Explain**
* Compare user top features vs title features
* Emit structured reasons:

```json
{
  "code": "shared_genre",
  "message": "Because you like neo-noir thrillers",
  "evidence": {"genres": ["Thriller", "Crime"]}
}
```

* UI renders `message`; analytics use `code`.

**5. Cache**
* Key: `slate:{user_id}:{context}:{profile_version}`
* TTL: short (e.g. 5–15 min) + invalidate on significant signal

### Strategy interfaces

```text
CandidateGenerator.generate(user_ctx) -> list[Candidate]
Scorer.score(user_ctx, candidates) -> list[Scored]
Diversifier.diversify(scored, k) -> list[Scored]
Explainer.explain(user_ctx, item) -> list[Reason]
```

Swap implementations without rewriting the API.

---

## Taste profile model (MVP)

### Feature families (start)

| Family | Examples | Weight source |
|--------|----------|---------------|
| Genre | action, drama… | like (+), dislike (−) |
| Keyword/theme | time-travel, found-footage | title keywords on liked titles |
| People | directors, lead actors | credits on liked titles |
| Temporal | decade, recency preference | release years of likes |
| Form | runtime buckets, movie vs TV | runtime / type |
| Popularity | blockbuster vs obscure | popularity of liked set |

### Update rule (simple, explainable)

* On **like**: pull title features toward user with learning rate α  
* On **dislike / not interested**: push away with β (usually smaller than α)  
* On **watchlist**: mild positive (γ < α)  
* Decay optional later; not required for MVP  
* Recompute dense vector from sparse weighted features after each batch of updates

**Why not pure collaborative day one?** Cold-start and data sparsity. Content + explicit taste works with one user. Collab is a Phase 4+ additive strategy.

---

## Data model (logical)

### Identity
* `users` — id, email, password_hash, created_at, onboarding_completed_at
* `refresh_tokens` — id, user_id, token_hash, expires_at, revoked_at

### Catalog
* `titles` — id, media_type, name, original_name, overview, release_date, runtime, popularity, vote_average, poster_path, backdrop_path, original_language, external_tmdb_id (unique), embedding vector
* `genres`, `title_genres`
* `people`, `credits` (title_id, person_id, job/character, billing_order)
* `keywords`, `title_keywords`

### Signals
* `user_title_interactions` — user_id, title_id, type (like|dislike|watchlist|not_interested|skip|view), weight, created_at  
  Unique constraint on (user_id, title_id, type) or upsert latest + append-only `interaction_events` if we need history

**Recommendation:**  
* `interaction_events` (append-only) for analytics & future learning  
* `user_title_state` (current state per user/title) for fast filters

### Taste
* `taste_profiles` — user_id, version, vector, features_json, updated_at

### Ops / product
* `onboarding_cards` or derive from curated seed lists
* `recommendation_impressions` (optional MVP+: user_id, title_id, slate_id, position, reasons, created_at) — critical for offline eval later; consider logging from day one (cheap, high value)

### Indexing (must-haves)
* Unique: users.email, titles.external_tmdb_id  
* FK indexes on all join tables  
* `user_title_state (user_id, state)`  
* pgvector IVFFlat/HNSW on `titles.embedding`  
* Redis keys namespaced by env

---

## API surface (MVP)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Access + refresh |
| POST | `/auth/refresh` | Rotate tokens |
| POST | `/auth/logout` | Revoke refresh |
| GET | `/me` | Current user + onboarding flag |
| GET | `/onboarding/cards` | Swipe deck |
| POST | `/onboarding/complete` | Submit likes/dislikes |
| GET | `/recommendations/for-you` | Main slate + reasons |
| GET | `/titles/{id}` | Detail |
| POST | `/titles/{id}/interactions` | like/dislike/watchlist/… |
| GET | `/watchlist` | Saved titles |
| GET | `/search?q=` | Title search |
| GET | `/health` | Liveness |
| GET | `/ready` | DB + Redis readiness |

All mutating routes: auth required, validated bodies, rate limited.

---

## Frontend structure

```
frontend/
  src/
    app/                 # routes, providers
    pages/               # Onboarding, Home, Title, Search, Watchlist, Auth
    components/          # UI primitives + domain components
    features/            # recommendation, auth, catalog hooks
    api/                 # typed client
    styles/
```

### UX flows (MVP)

1. **Land → signup/login**
2. **Onboarding swipe** (cannot be skipped permanently without weak cold-start; allow “quick start” with 3 mood packs if swipe fatigue)
3. **Home “For you”** — poster grid/cards, reasons under title or on long-press/expand
4. **Detail** — overview, cast snippet, actions
5. **Watchlist / Search** — secondary nav

**Performance UX:** skeleton loaders, optimistic interaction UI, cached slate stale-while-revalidate.

---

## Security baseline

* Password hashing: Argon2id (or bcrypt if deploy constraints)
* JWT access: short TTL (e.g. 15m), refresh: days, **rotate + hash store**
* HttpOnly secure cookies **or** bearer with strict XSS hygiene (prefer httpOnly cookies for browser SPA if same-site setup allows; document choice at implement time)
* CORS allowlist (Vercel prod + localhost)
* Rate limit: auth endpoints aggressively; rec endpoints moderately (Redis)
* Pydantic validation everywhere
* No secrets in repo; `.env.example` only
* SQLAlchemy parameterization (no raw string SQL)
* Security headers via reverse proxy / framework middleware

---

## Performance baseline

| Concern | Approach |
|---------|----------|
| Slate latency | Redis cache keyed by profile version |
| ANN | pgvector HNSW; limit candidate set (e.g. 200) before re-rank |
| N+1 | Eager load genres/reasons; batch queries |
| Catalog images | CDN URLs from TMDb; don’t proxy bytes through API |
| Pagination | Cursor/limit on search & watchlist |
| Profile updates | Sync for onboarding; debounce for rapid taps if needed |

---

## Observability

* Structured JSON logs: `request_id`, `user_id`, `route`, `latency_ms`
* Metrics later: Prometheus/OpenTelemetry when deploy target chosen
* Error tracking: Sentry (post-MVP ok, stub interface early)
* Health: `/health` process up; `/ready` dependencies

---

## Local & deploy

### Local
* `docker compose up` → API, Postgres+pgvector, Redis
* Frontend: `npm run dev` → Vite proxy to API

### Deploy
* **API + worker + Postgres + Redis:** Railway or Render  
* **Frontend:** Vercel  
* **CI:** GitHub Actions — lint, unit tests, build images  

### Environments
* `local` / `staging` / `production` via env vars only

---

## Testing strategy

| Layer | What |
|-------|------|
| Unit | Scorer, diversifier, explainer, taste update math |
| Integration | API + test DB (Postgres service in CI) |
| Contract | OpenAPI schema stability for critical routes |
| Manual | Onboarding → first slate → reason quality |

No need for full E2E in Phase 0–1; add Playwright when UI stabilizes.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| TMDb rate limits / ToS | Cache aggressively; batch ingest; respect attribution |
| Cold start | Strong onboarding; mood packs; popular-quality hybrid fallback |
| Filter bubble | MMR + exploration quota from day one |
| Over-engineering recs | Single pipeline, strategy hooks, offline eval before complexity |
| Embeddings quality | Start with structured+text features; re-embed batch when model improves |
| Cost at scale | Cache slates; don’t recompute ANN every click |

---

## Decision log (initial)

| Decision | Choice | Why |
|----------|--------|-----|
| Monorepo | Yes | One product, shared types later optional, simpler for small team |
| Catalog source | TMDb | Best coverage/cost for indie; not the product |
| Vectors | pgvector in Postgres | One system to operate at MVP scale |
| Rec approach | Content + taste + diversify | Works with N=1 user; explainable |
| Collab filtering | Later | Needs density we won’t have at launch |
| Auth | Email + JWT refresh | Simple, portable; OAuth later |
| Workers | CLI first | Avoid distributed systems until jobs hurt |

---

## What we deliberately reject (for now)

* Microservice split per domain  
* Kafka/event bus  
* Separate “ML service” process  
* GraphQL (REST is enough and clearer for caching)  
* Building our own user-uploaded video catalog  

Revisit only with a concrete scaling or team pain signal.
