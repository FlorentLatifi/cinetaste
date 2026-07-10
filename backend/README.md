# Backend (FastAPI)

Python API for auth, catalog, taste profiles, and recommendations.

## Layout

```
app/
  main.py                 # composition root
  api/                    # HTTP routers & schemas
  application/            # use cases (AuthService, …)
  domain/                 # exceptions / pure domain
  infrastructure/         # SQLAlchemy models, Redis, session
  recommendation/         # strategies (next phases)
alembic/                  # migrations
tests/
```

## Run with Docker (recommended)

From repo root:

```powershell
docker compose up --build
```

## Run locally (API only)

Requires Postgres + Redis from Compose:

```powershell
docker compose up db redis -d
.\.venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Load env from repo-root `.env` (`../.env` is supported by settings).

## Tests

```powershell
pytest
```

## Status

* Auth + health/ready: done
* Full catalog / taste / rec pipeline: next
