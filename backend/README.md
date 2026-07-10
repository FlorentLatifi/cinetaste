# Backend (FastAPI)

Python API for auth, catalog, taste profiles, and recommendations.

## Planned layout

```
app/
  main.py
  api/                 # HTTP routers & request/response schemas
  application/         # use cases
  domain/              # entities, ports
  infrastructure/      # SQLAlchemy, Redis, TMDb, security
  recommendation/      # candidates, scoring, diversification, explanations
```

## Stack

* FastAPI + Uvicorn
* SQLAlchemy 2.x + Alembic
* PostgreSQL + pgvector
* Redis
* Pydantic Settings

## Status

Scaffold not created yet — see `docs/ARCHITECTURE.md` for target design.

Implementation order:

1. Project skeleton + Docker Compose (API, Postgres, Redis)
2. Auth + users
3. Catalog models + TMDb ingest CLI
4. Interactions + taste profile
5. Recommendation pipeline + `/recommendations/for-you`
