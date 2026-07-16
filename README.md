# CineTaste — Movie & TV Recommendation Platform

> Real product goal: understand **your taste**, recommend **explainable** picks, and help you **discover** what you’d miss on generic platforms.

**Working name:** CineTaste (placeholder)

---

## North star

We are **not** a TMDb browser.

We are a **taste profile** product:

1. Learn preferences with low friction (swipe onboarding)
2. Rank titles for *you*
3. Diversify and explore (avoid filter bubbles)
4. Explain every recommendation in human language
5. Improve from every like, dislike, and save

---

## Stack

| Layer | Tech |
|-------|------|
| API | Python, FastAPI, SQLAlchemy, Alembic |
| Data | PostgreSQL + pgvector, Redis |
| Web | React, TypeScript, Vite |
| Ops | Docker Compose (local + staging + prod-like), GitHub Actions, Render + Vercel |

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Product/MVP: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Agent constitution: [`AGENTS.md`](AGENTS.md)

---

## Repository layout

```
cinetaste/
├── AGENTS.md
├── docker-compose.yml           # local dev
├── docker-compose.staging.yml   # isolated staging stack
├── docker-compose.prod.yml      # prod-like local
├── .env.example / .env.staging.example / .env.production.example
├── docs/                        # PRODUCT, ARCHITECTURE, DEPLOY, …
├── backend/                     # FastAPI app
└── frontend/                    # React + Vite SPA
```
---

## Current status

**Phase 2 — Core product loop (local MVP)**

- [x] Constitution, product, architecture docs
- [x] Docker Compose: API, Postgres+pgvector, Redis
- [x] Alembic initial schema (users, catalog, signals, taste vectors)
- [x] Auth: register / login / refresh / logout + `/me`
- [x] Frontend: auth shell, protected home, design system baseline
- [x] Catalog ingest (TMDb) + offline demo seed
- [x] Swipe onboarding + taste profile builder
- [x] Recommendation pipeline (score + MMR diversity + explanations)
- [x] For You feed, watchlist, interactions
- [x] CI (GitHub Actions) + rate limits + security headers
- [x] Deploy blueprints (Render prod + staging, Vercel) + local staging compose — see `docs/DEPLOY.md`
- [x] httpOnly refresh cookies, Sentry optional, soft-launch checklist
- [ ] Live hosted deploy (you click connect on Render/Vercel)

---

## Quick start

### Prerequisites

* Docker Desktop running
* Node 20+ (for frontend)
* Optional: Python 3.12+ if running API outside Docker

### 1. Environment

```powershell
cd C:\Users\flore\movie-rec-platform
copy .env.example .env
# Edit JWT_SECRET (min 32 chars) and later TMDB_API_KEY
```

### 2. Start API + data plane

```powershell
docker compose up --build
```

* API: http://localhost:8000  
* Docs: http://localhost:8000/docs  
* Health: http://localhost:8000/api/v1/health  
* Ready: http://localhost:8000/api/v1/ready  

Migrations run automatically on API container start.

### 3. Seed catalog

**Option A — demo data (no API key):**

```powershell
docker compose exec api python -m app.scripts.seed_demo_catalog
```

**Option B — real TMDb data:**

1. Get a free key: https://www.themoviedb.org/settings/api  
2. Set `TMDB_API_KEY=...` in `.env`  
3. Restart API, then:

```powershell
docker compose exec api python -m app.scripts.ingest_catalog --pages 3
```

### 4. Start frontend

```powershell
cd frontend
npm install
npm run dev
```

App: http://localhost:5173 (proxies `/api` → backend)

Flow: **Register → Onboarding swipe → For You (with reasons) → Like / Save / Pass**

### 4. Backend unit tests (local venv)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pytest
```

---

## API surface (live now)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/ready` | DB + Redis |
| POST | `/api/v1/auth/register` | Email + password |
| POST | `/api/v1/auth/login` | Returns access + refresh |
| POST | `/api/v1/auth/refresh` | Rotating refresh |
| POST | `/api/v1/auth/logout` | Revoke refresh |
| GET | `/api/v1/me` | Bearer access token |

---

## Principles (short)

* Optimize for the user, not the task
* Prefer the simplest solution that stays production-honest
* Explainability and discovery are product requirements
* Challenge weak designs; reduce friction; avoid over-engineering

---

## License

TBD
