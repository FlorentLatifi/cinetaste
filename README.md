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
| Ops | Docker, GitHub Actions, Railway/Render + Vercel |

Architecture: clean/onion layering, repository pattern, strategy pattern for recommenders, dependency injection.

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Product/MVP: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Agent constitution: [`AGENTS.md`](AGENTS.md)

---

## Repository layout

```
movie-rec-platform/
├── AGENTS.md                 # Product constitution (for humans + AI agents)
├── README.md
├── docs/
│   ├── PRODUCT.md            # Vision, MVP, metrics, roadmap
│   └── ARCHITECTURE.md       # System design, schema, rec pipeline
├── backend/                  # FastAPI application (scaffold next)
├── frontend/                 # React + Vite application (scaffold next)
└── .github/workflows/        # CI (added as we harden)
```

---

## Current status

**Phase 0 — Foundation**

- [x] Project constitution
- [x] Product vision & MVP lock
- [x] Architecture & recommendation design
- [ ] Backend scaffold (FastAPI + Docker Compose)
- [ ] Frontend scaffold (React + Vite)
- [ ] Auth, catalog ingest, taste + onboarding, For You feed

---

## Local development (upcoming)

Once scaffolded:

```bash
# Infrastructure + API
docker compose up --build

# Frontend (from frontend/)
npm install
npm run dev
```

Environment variables will be documented in `.env.example` files.

---

## Principles (short)

* Optimize for the user, not the task
* Prefer the simplest solution that stays production-honest
* Explainability and discovery are product requirements
* Challenge weak designs; reduce friction; avoid over-engineering

---

## License

TBD
