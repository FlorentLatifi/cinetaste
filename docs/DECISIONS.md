# Decision log

Lightweight ADR-style log. Newest first.

---

## 2026-09-24 — The taste rebuild happens after the response

### Decision
`POST /titles/{id}/interactions` writes the event, bumps `profile.version` and
invalidates the cache inside the request; the profile itself is rebuilt in a
FastAPI background task afterwards. `TASTE_RECOMPUTE_DEFERRED=false` restores
the old inline behaviour.

### Rationale
The rebuild re-reads the entire interaction history and every title it
mentions, so the people who use the product most waited longest for the
simplest action:

    history    inline    deferred
        100   92.8 ms     19.4 ms
        500  177.1 ms     18.1 ms
      1,000  311.7 ms     16.3 ms
      2,000  558.0 ms     17.8 ms

Deferred is flat, because what stays in the request no longer depends on how
much the user has rated.

### Why `recompute=False` alone was not the answer
The For You cache is keyed by `profile.version`. A rating that skips the
rebuild also skips the bump, so the next slate is byte-identical — the user
rates a film and nothing on screen changes. Slower is better than that. The
two jobs are separated instead: bump now, rebuild behind.

Nothing the user sees immediately came from the profile anyway. The rated
title leaves the feed because of `user_title_state`, undo reads the event log,
and history reads the same rows.

### Known failure mode
If the process dies between the response and the task, that rebuild is lost:
the profile is one interaction stale while its version says otherwise. Nothing
is corrupted, and the next rating rebuilds it. `taste_recompute_deferred_failed`
in the logs is how you would know. Accepted rather than solved, because solving
it properly means a durable queue — more moving parts than this trade is worth
at this scale.

### Rejected alternative
**Incremental vector updates.** A rating is a weighted vector add, so in
principle the profile could be nudged rather than rebuilt. But
`effective_title_signals` keeps only the strongest event per title and applies
time decay across the whole history, so a new event can *replace* an older
one — undoing its contribution needs that contribution stored per title. Real
work for a path that is now ~18 ms.

### Status
Active, on by default.

---

## 2026-09-23 — Two rate limiters, because an IP is not an identity

### Decision
Keep the per-IP limiter, and add a second one keyed by the **account** for
login and password reset (`backend/app/core/throttle.py`). Both apply; either
can reject.

### Rationale
Deriving a client IP behind a proxy means trusting `X-Forwarded-For` from a
fixed offset, which only holds while the request passes through the whole
chain. Our origin host stays publicly reachable, so it often does not — and
the old fallback handed the caller its own bucket key, which a forged header
could then cycle per request. Measured against the production setting:

    X-Forwarded-For: 1.1.1.1, 2.2.2.2   peer: 198.51.100.66   ->  bucket "1.1.1.1"

`client_ip` now falls back to the socket peer on a short chain and rejects
non-addresses. That closes the short chain but **not** a forged full-length one
from a caller reaching the origin directly, and no header parsing will. An
account-keyed counter is immune to both that and to a spread of source
addresses.

### Rules locked
* Only **failed** attempts count, so an active user is never locked out.
* Identifiers are hashed before becoming keys — no email in Redis or in logs.
* Reads fail **closed** on credential routes (cannot count = cannot tell
  attacker from user); writes fail **open** (a lost increment must not turn a
  correct rejection into a 500).
* Every rejection is worded identically across scopes, so the response never
  confirms an address is registered.

### Rejected alternatives
* **Trust one hop instead of two** — buckets every CDN-proxied user together.
* **`FORWARDED_ALLOW_IPS=*`** — worse: uvicorn then takes the left-most entry.
* **A shared secret from the edge** — the correct fix, but `vercel.json`
  rewrites cannot add request headers; it needs Edge Middleware.

### Status
Active. The Edge Middleware option is listed as a known gap in
[SECURITY.md](SECURITY.md).

---

## 2026-09-23 — Email verification ships off by default

### Decision
Build the whole flow (`email_verified_at`, one-time hashed tokens,
`/auth/verify-email`, `/auth/resend-verification`, a landing page) but gate
enforcement behind `REQUIRE_EMAIL_VERIFICATION=false`.

### Rationale
Registration accepted any address, so an account could be opened under someone
else's email — and that person would then receive the reset mail for an account
they never made. But there is no SMTP configured yet, and turning enforcement on
without it locks every account out with no path to recovery. Production refuses
to start in that combination rather than discovering it in traffic.

### Rules locked
* When enforced, only the **product** surface is gated. `/me`, taste export,
  resending the link and deleting the account stay reachable, or an unverified
  user has no way forward and no way out.
* `/auth/resend-verification` requires a session: an endpoint taking an email
  would mail anyone on request and confirm which addresses are registered.
* Unknown, used and expired tokens all fail identically.

### Status
Active, dormant until SMTP exists.

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
