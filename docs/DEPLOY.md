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

1. Open [Render](https://render.com) → **New** → **Blueprint**
2. Connect `FlorentLatifi/cinetaste`
3. Apply `render.yaml`
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

### Resilience notes (production)

| Concern | Behavior |
|---------|----------|
| Redis down | `/recommendations/for-you` still computes (no cache). Auth routes **fail closed** (503) if rate limiter cannot reach Redis. |
| Taste onboarding | Reactions validated first; profile recomputed **once** after all events. |
| Proxy headers | Set `FORWARDED_ALLOW_IPS` to your LB/proxy IPs (not `*` in hostile networks). |

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

**Still recommended before a large launch:**

- httpOnly cookie session transport (vs localStorage tokens)
- WAF / CDN in front of API
- Automated DB backups verification
- Sentry (or similar) error tracking
- Staging environment mirror

---

## 7. CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR:

- Ruff + pytest (with Postgres/pgvector + Redis services)
- Frontend typecheck/build

Merge only when CI is green.

---

## 8. Local production-like smoke

```powershell
copy .env.production.example .env.production
# fill secrets
docker compose -f docker-compose.prod.yml --env-file .env.production up --build
```
