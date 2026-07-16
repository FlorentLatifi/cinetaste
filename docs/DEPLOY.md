# Deploy guide — CineTaste production

Goal: host a **public beta** that is secure enough for real users, without overbuilding ops.

Recommended split (matches constitution):

| Piece | Host |
|-------|------|
| Frontend (React SPA) | **Vercel** |
| API (FastAPI) | **Render** or **Railway** |
| Postgres + pgvector | Managed DB on same host |
| Redis | Managed Redis on same host |

---

## Prerequisites

1. GitHub repo: `FlorentLatifi/cinetaste` (done)
2. Strong secrets generated locally:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

3. Optional but recommended for real catalog: [TMDb API key](https://www.themoviedb.org/settings/api)

---

## 1. Backend on Render (blueprint)

**Production:** `render.yaml` → services `cinetaste-api`, `cinetaste-db`, `cinetaste-redis`  
**Staging:** `render.staging.yaml` → services `*-staging` (see §9d)

1. Open [Render](https://render.com) → **New** → **Blueprint**
2. Connect `FlorentLatifi/cinetaste`
3. Apply `render.yaml` for production (or `render.staging.yaml` for staging)
4. Set secrets in the dashboard:
   - `CORS_ORIGINS` = your Vercel URL (e.g. `https://cinetaste.vercel.app`)
   - `TMDB_API_KEY` (optional at first)
   - Confirm `JWT_SECRET` was generated
5. After first deploy, open a shell / connect to Postgres and ensure:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Migrations run automatically via `entrypoint.sh` (`alembic upgrade head`), including the **HNSW** index on `titles.embedding` for ANN candidate generation.

After a large catalog ingest, optional (usually automatic on first queries):

```sql
-- Verify index exists
SELECT indexname FROM pg_indexes WHERE tablename = 'titles' AND indexname LIKE '%embedding%';
```

6. Liveness: `https://<api-host>/api/v1/health` (process up)  
7. Readiness: `https://<api-host>/api/v1/ready` — returns **503** if DB (or Redis in production) is down; use this for load balancers  

### Where to watch

| Item | Notes |
|------|--------|
| Endpoint | `GET /titles/{id}/where-to-watch?region=US` |
| Source | TMDb watch providers (JustWatch-sourced); SPA shows Stream / Free / Rent / Buy |
| Config | `TMDB_API_KEY` required for live data; `WATCH_PROVIDER_REGION` default country |
| Cache | Redis key `watch_providers:{movie\|tv}:{tmdb_id}:{region}` (default TTL 12h) |
| Failure mode | Empty `available=false` if key missing or TMDb down — title detail still loads |
| Attribution | UI shows “Streaming data by JustWatch via TMDb” |

### Resilience notes (production)

| Concern | Behavior |
|---------|----------|
| Redis down | `/recommendations/for-you` still computes (no cache). Auth routes **fail closed** (503) if rate limiter cannot reach Redis. |
| Taste onboarding | Reactions validated first; profile recomputed **once** after all events. |
| Proxy headers | Set `FORWARDED_ALLOW_IPS` to your LB/proxy IPs (not `*` in hostile networks). |

### Observability (Sentry)

Optional but recommended for public beta.

| Surface | Env var | Notes |
|---------|---------|--------|
| API | `SENTRY_DSN` | Leave empty to disable. `sentry-sdk` no-ops without DSN. |
| API | `SENTRY_TRACES_SAMPLE_RATE` | Default `0.1` (10% of requests) |
| API | `SENTRY_RELEASE` | Optional git SHA / version tag |
| SPA (Vercel) | `VITE_SENTRY_DSN` | Build-time; baked into the bundle |
| SPA | `VITE_SENTRY_TRACES_SAMPLE_RATE` | Default `0.1` |

Request logs already emit `method path status duration_ms request_id` — pair with Render log drains or a metrics scraper for basic RED signals when Sentry is off.

### Account lifecycle

| Feature | Endpoint | Notes |
|---------|----------|--------|
| Forgot password | `POST /auth/forgot-password` | Always generic success (no email enumeration). Sends via SMTP if configured, else log-only. |
| Reset password | `POST /auth/reset-password` | One-time token; revokes all sessions. |
| Delete account | `DELETE /me` | Requires password + confirm `DELETE`; cascades taste/interactions via FKs. |
| Refresh reuse | `POST /auth/refresh` | Reusing a rotated refresh token revokes the **whole family** (`refresh_reuse`). |

Set `PUBLIC_APP_URL` to your SPA origin so reset links are correct.

**SMTP (optional):** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`.  
If `SMTP_HOST` is empty, password-reset messages go to application logs only.

### Auth cookies (SPA)

| Item | Value |
|------|--------|
| Refresh token | **httpOnly** cookie `ct_refresh` (not in JSON / not localStorage) |
| Access token | Short-lived JWT in SPA memory only |
| Cookie path | `{API_PREFIX}/auth` |
| Production | `Secure; SameSite=None` (cross-site Vercel → API host) |
| Local | `SameSite=Lax` (localhost ports) |
| CORS | `allow_credentials=true` + explicit `CORS_ORIGINS` (never `*`) |

Frontend must call the API with `credentials: "include"`.

### Database URL note

SQLAlchemy expects async URLs:

```
postgresql+asyncpg://...
```

If the host injects `postgresql://...`, add a small transform or set:

```
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
```

(Render sometimes provides `postgres://` — convert scheme to `postgresql+asyncpg`.)

---

## 2. Frontend on Vercel

1. Import the GitHub repo in Vercel
2. **Root directory:** `frontend`
3. Framework: Vite
4. Build: `npm run build` · Output: `dist`
5. Environment variable:

```
VITE_API_BASE_URL=https://<your-api-host>/api/v1
```

6. Deploy → copy the production URL into API `CORS_ORIGINS`
7. Redeploy API if CORS changed
8. Confirm browser login sets `ct_refresh` cookie on the **API** host (Application → Cookies)

`frontend/vercel.json` enables SPA fallback + basic security headers.

---

## 3. Railway alternative (API)

1. New project from GitHub
2. Add **Postgres** (install pgvector extension) + **Redis**
3. Deploy Docker service from `/backend`
4. Set env vars from `.env.production.example`
5. Map `PORT` automatically (entrypoint respects `$PORT`)

---

## 4. Production env checklist

| Variable | Required | Notes |
|----------|----------|--------|
| `APP_ENV=production` | Yes | Enables safety validators |
| `APP_DEBUG=false` | Yes | Hides stack traces / OpenAPI |
| `JWT_SECRET` | Yes | ≥48 chars, unique |
| `CORS_ORIGINS` | Yes | Exact frontend origin(s) |
| `DATABASE_URL` | Yes | asyncpg URL |
| `REDIS_URL` | Yes | Rate limit + slate cache |
| `TMDB_API_KEY` | Recommended | Real catalog |
| `RATE_LIMIT_*` | Optional | Defaults are sane |

Startup **fails closed** if production secrets look like local defaults.

---

## 5. Post-deploy product steps

```bash
# Inside API container / one-off job
python -m app.scripts.seed_demo_catalog
# or
python -m app.scripts.ingest_catalog --pages 3
```

Then:

1. Register a test user on the live frontend  
2. Complete onboarding  
3. Verify For You returns reasons  
4. Confirm `/api/v1/ready` shows db+redis ok  

---

## 6. Security baseline (what we ship)

- Argon-level intent via bcrypt password hashing  
- Short-lived access JWT + rotating hashed refresh tokens  
- Rate limits (auth stricter) via Redis  
- Security headers middleware  
- OpenAPI/docs disabled in production  
- Generic 500 responses (no stack traces)  
- Catalog ingest API disabled when `APP_ENV=production`  
- Non-root Docker user + healthcheck  

**Already in place for beta:**

- httpOnly refresh cookie + in-memory access JWT  
- Optional Sentry (`SENTRY_DSN` / `VITE_SENTRY_DSN`)  
- CI with Postgres/pgvector + Redis integration tests  
- axe gate on guest SPA routes  

**Already documented:**

- Staging stack: `docker-compose.staging.yml` + `.env.staging.example` (see §9)  
- Hosted staging blueprint: `render.staging.yaml` (see §9d)  
- Authenticated axe + interaction smokes in CI (`npm run test:a11y`)  

**Still recommended before a large launch:**

- WAF / CDN in front of API  
- Automated DB backups verification (host-managed snapshots)  

---

## 6b. Soft-launch checklist (public beta)

Use this before inviting real users beyond friends/family.

### Pre-flight

| Check | How |
|-------|-----|
| CI green on `master` | GitHub Actions |
| API `/ready` returns 200 with db + redis | `curl https://api…/api/v1/ready` |
| SPA loads over HTTPS | Vercel production URL |
| CORS matches SPA origin exactly | API env `CORS_ORIGINS` |
| `JWT_SECRET` strong & unique | ≥48 chars, not in git |
| `TMDB_API_KEY` set if you expect a real catalog | Render/Railway secrets |
| Password reset path known | SMTP configured **or** log-only tokens acceptable |
| Sentry optional | Blank DSN = disabled |

### Smoke path (10 minutes)

1. Register → verify refresh cookie on **API** host  
2. Complete onboarding (≥6 ratings, ≥2 positive)  
3. For You shows cards with **Why this pick** (and optional Hidden gem / Discovery badges)  
4. Pass / Not interested → toast **Undo** restores card  
5. Title detail → **Watched** opens rate strip; rating or skip-rating + Undo works; where-to-watch region works (or soft-empty without key)  
6. Search finds a known title; open detail; Similar row loads  
7. History filters + Clear; Account **Your taste** chips appear after ratings  
8. Account: password reset flow (or log token in API logs)  
9. Sign out → cannot hit For You without re-login  

### Backups (host-managed)

| Host | Action |
|------|--------|
| **Render Postgres** | Enable point-in-time / daily backups on the paid plan; note restore docs |
| **Railway Postgres** | Use platform backups or `pg_dump` cron to object storage |
| **Redis** | Ephemeral OK (cache + rate limits); no durable user data |
| **Verify** | Once before launch: restore a dump into a scratch DB and run migrations |

Optional dump from a shell with network access:

```bash
pg_dump "$DATABASE_URL_SYNC" -Fc -f cinetaste-$(date +%Y%m%d).dump
```

(Use a `postgresql://` URL for `pg_dump`, not `postgresql+asyncpg://`.)

### After invite

- Watch Sentry / host logs for 5xx and auth spikes  
- Confirm rate limits don’t false-positive on shared NATs  
- Keep catalog ingest **off** in production (`APP_ENV=production`)  

---

## 7. CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR:

- Ruff + pytest (with Postgres/pgvector + Redis services)
- Frontend typecheck/build + Playwright axe on guest routes

Merge only when CI is green.

---

## 8. Local production-like smoke

```powershell
copy .env.production.example .env.production
# fill secrets
docker compose -f docker-compose.prod.yml --env-file .env.production up --build
```

---

## 9. Staging environment

Goal: a **production-shaped** stack that does not share Postgres/Redis volumes with local
dev (`docker-compose.yml`) or prod compose, so you can break things safely.

| Concern | Local staging | Hosted staging (optional) |
|---------|---------------|---------------------------|
| Compose | `docker-compose.staging.yml` | Second Render Blueprint / service names |
| Env file | `.env.staging` (from `.env.staging.example`) | Separate dashboard secrets |
| `APP_ENV` | `staging` (not `production` — looser boot validators) | `staging` or `production` if you want full checks |
| API port | **8001** (default) | Render URL |
| Postgres | host **5433** → container 5432 | Separate DB instance |
| Redis | host **6380** → container 6379 | Separate Redis |
| SPA | local Vite or Vercel Preview | Vercel Preview / `*-staging` project |
| Data | volume `postgres_staging_data` | never point at prod DB |

### 9a. Start staging API locally

```powershell
cd cinetaste
copy .env.staging.example .env.staging
# set POSTGRES_PASSWORD + JWT_SECRET (and TMDB_API_KEY if testing catalog)

# ENV_FILE points the API service at your secrets file (defaults to the example).
$env:ENV_FILE=".env.staging"
docker compose -p cinetaste-staging -f docker-compose.staging.yml --env-file .env.staging up --build
```

Checks:

```powershell
curl http://127.0.0.1:8001/api/v1/health
curl http://127.0.0.1:8001/api/v1/ready
```

Point the SPA at staging:

```powershell
cd frontend
# .env.local or shell:
$env:VITE_API_BASE_URL="http://127.0.0.1:8001/api/v1"
npm run dev
```

Ensure `CORS_ORIGINS` in `.env.staging` includes `http://localhost:5173` (and any preview URL).

### 9b. Seed / migrate

Migrations run on API start (`entrypoint.sh` → `alembic upgrade head`).

Optional catalog (with `TMDB_API_KEY` set):

```powershell
docker compose -p cinetaste-staging -f docker-compose.staging.yml --env-file .env.staging exec api `
  python -m app.scripts.seed_demo_catalog
```

### 9c. Tear down staging (wipe data)

```powershell
docker compose -p cinetaste-staging -f docker-compose.staging.yml down -v
```

### 9d. Hosted staging (Render + Vercel)

Repo file: **`render.staging.yaml`** (distinct service names from production `render.yaml`).

| Resource | Staging name |
|----------|----------------|
| Web | `cinetaste-api-staging` |
| Postgres | `cinetaste-db-staging` |
| Redis | `cinetaste-redis-staging` |

1. **Render → New → Blueprint** → connect this repo → apply **`render.staging.yaml`**  
   (do **not** re-apply production `render.yaml` into the same staging stack).  
2. Dashboard secrets after first deploy:  
   - `CORS_ORIGINS` = fixed staging SPA origin (see Vercel below)  
   - `PUBLIC_APP_URL` = same origin  
   - `TMDB_API_KEY` (optional)  
   - `SENTRY_DSN` (optional; prefer a staging project)  
   - `JWT_SECRET` is auto-generated — keep it distinct from production  
3. On the staging DB shell: `CREATE EXTENSION IF NOT EXISTS vector;`  
4. **Vercel:** create a **second project** or a production branch alias  
   (e.g. `cinetaste-staging` → `https://cinetaste-staging.vercel.app`).  
   Avoid relying on one-off Preview URLs for CORS (`https://*.vercel.app` is not valid).  
5. Vercel env for that project:  
   `VITE_API_BASE_URL=https://cinetaste-api-staging.onrender.com/api/v1`  
   (adjust host to whatever Render assigned).  
6. Smoke the soft-launch path (§6b) against staging before promoting to production.  
7. **Never** share production `DATABASE_URL` or `JWT_SECRET` with staging.

### 9e. Staging vs production checklist

| Item | Staging | Production |
|------|---------|------------|
| Isolated DB + Redis | Required | Required |
| Strong unique `JWT_SECRET` | Required | Required (≥48 chars enforced when `APP_ENV=production`) |
| Real user data | Synthetic / throwaway | Real |
| Catalog ingest | Allowed for testing | Disabled when `APP_ENV=production` |
| SMTP | Log-only OK | Real SMTP recommended |
| Soft-launch smoke (§6b) | Run here first | Run again after promote |

Promote path: green CI → staging smoke (§6b) → production deploy → production smoke.