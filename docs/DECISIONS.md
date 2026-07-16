# Decision log

Lightweight ADR-style log. Newest first.

---

## 2026-07-17 — Taste signal policy as code

### Decision
Centralize all interaction → taste effects in `backend/app/domain/taste_signals.py`, with product documentation in `docs/TASTE_SIGNALS.md`.

### Rules locked
* **Haven't seen** = weight `0`, no taste update, not excluded from For You.
* **Not interested** = mild negative (`−0.40`), excluded from For You.
* **Ratings** Bad→Favorite = `rate_1`…`rate_4` with weights `−0.90 … +1.55`.
* **Watchlist** = mild positive intent (`+0.45`), not a quality judgment.
* Future **watched** family reserved in the same table.

### Rationale
One policy table prevents drift between onboarding, interactions API, recompute, feed filters, and explanations.

### Status
Active.

---

## 2026-07-10 — Foundation lock

### Product name
**Decision:** Working name **CineTaste** (placeholder).  
**Rationale:** Unblocks docs/repo; brand can change without architecture cost.  
**Status:** Open for rename.

### Catalog source
**Decision:** TMDb as primary catalog ingest for MVP.  
**Rationale:** Coverage, cost, community standard for indie apps.  
**Constraint:** Product value is taste + ranking, not TMDb UI. Attribution & rate limits required.

### Recommendation approach (MVP)
**Decision:** Content-based taste vector + feature boosts + MMR diversification + structured explanations.  
**Rationale:** Works at N=1 user; cold-start solvable; explainable.  
**Deferred:** Collaborative filtering until density exists.

### Vector storage
**Decision:** pgvector inside PostgreSQL.  
**Rationale:** One operational database at MVP scale; fewer moving parts than a separate vector DB.  
**Revisit when:** ANN latency or scale demands dedicated vector infra.

### Auth (MVP)
**Decision:** Email/password + short-lived access JWT + rotating refresh tokens (hashed at rest).  
**Rationale:** Portable, simple, secure enough for launch.  
**Deferred:** Google OAuth until signup friction measured.

### Media scope
**Decision:** Schema supports movies **and** TV from day one; UI may prioritize movies if needed.  
**Rationale:** Schema dual-type is cheap; excluding TV later is wasteful.

### Monorepo
**Decision:** Single repo `backend/` + `frontend/` + `docs/`.  
**Rationale:** One product, one team, simpler CI and agent context.

### Architecture complexity
**Decision:** Onion/clean + repository + strategy + DI only. No CQRS/event bus/microservices for MVP.  
**Rationale:** Constitution + decision framework — complexity must earn its place.

### Interaction storage
**Decision:** Prefer append-only `interaction_events` + current `user_title_state` when implementing.  
**Rationale:** Analytics/eval need history; filters need current state. Slight schema cost, high long-term value.
