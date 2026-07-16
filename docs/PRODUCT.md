# Product Vision & MVP

**Working name:** CineTaste *(placeholder — rename anytime)*  
**One-liner:** Discover movies & TV that match *your* taste — and always know why.

---

## Problem

Streaming libraries are huge. Search and “top 10” lists fail people who:

* Don’t know what they want until they see it
* Are tired of the same franchises and filter bubbles
* Don’t trust black-box “Recommended for you”
* Want to find **hidden gems**, not just popular hits

Generic platforms optimize for watch-time and licensing. We optimize for **taste fit + discovery + trust**.

---

## Target users (MVP)

| Persona | Need | What success looks like |
|---------|------|-------------------------|
| **Explorer** | “I like good stuff but I’m bored of the usual” | Finds something unexpected they love within 2 minutes |
| **Decisive dreader** | Decision fatigue every night | One clear “watch tonight” slate with reasons |
| **Taste-conscious** | Cares about vibe, directors, pacing | Feels understood after a short onboarding |

**Non-goal for MVP:** social network, group watch, critic community, full media player.

---

## Product principles (user-facing)

1. **Zero-friction preference capture** — never force “search 20 titles.”
2. **Explain every pick** — trust > magic score.
3. **Discover, don’t only reinforce** — relevance + diversity + exploration.
4. **Feels fast** — perceived latency under ~300ms for cached slates; progressive load otherwise.
5. **Gets smarter quietly** — every like/dislike/watchlist updates taste.

---

## Core product loop

```
Onboard (swipe / starter packs)
    → Build Taste Profile
    → Personalized + diversified slate
    → Show human reasons
    → Capture feedback (like / dislike / save / skip / not interested)
    → Update Taste Profile
    → Better next slate
```

If this loop is weak, no amount of pages will save the product.

---

## MVP scope (ship this first)

### In MVP

| Area | Capability | User problem solved |
|------|------------|---------------------|
| Auth | Email/password + secure JWT (access + refresh) | Private profiles & history |
| Catalog | Movies + TV basics (title, poster, year, genres, overview, key cast/crew) | Something to recommend from |
| Onboarding | Swipe-style like/dislike on ~12–20 curated cards + optional starter moods | Preference data without search |
| Taste profile | Weighted signals: genres, keywords, people (cast/crew), year/runtime buckets, popularity preference | System “understands me” |
| Home feed | “For you” slate (e.g. 20 items) with diversity + light exploration | Instant value |
| Explanations | 1–3 short reasons per item | Trust |
| Detail | Title page: metadata, reasons, actions | Decide to watch / save |
| Actions | Like, dislike, watchlist, not interested | Explicit + training signals |
| Search | Simple title search (secondary) | Power users / known titles |
| Performance | Redis cache for slates + catalog hot paths | Speed |
| Ops | Docker, health checks, structured logs, CI lint/test | Production path |

### Explicitly out of MVP (postpone)

* Collaborative filtering (“users like you”)
* Full multi-armed bandit infrastructure
* Social features, lists sharing, friends
* Live streaming / deep links to every platform’s player
* Advanced mood NLP from free text
* Multi-language UX (catalog language filters OK)
* Mobile native apps
* Admin CMS UI (seed scripts / CLI enough)
* Real-time collaborative sessions
* Complex CQRS / event sourcing

These are valuable later; they do not prove product-market fit faster than the core loop.

---

## Success metrics

### Product (north-star oriented)

| Metric | MVP target (directional) |
|--------|---------------------------|
| **Onboarding completion** | ≥ 70% of signups finish swipe set |
| **Time-to-first-save/like post-onboarding** | < 60s median |
| **Feedback rate** | ≥ 30% of shown cards get an action in session |
| **D7 return** | Track; aim to improve week-over-week |
| **“Why” engagement** | % of users who expand/view reasons (qualitative early) |

### Quality (recommendation)

| Metric | Notes |
|--------|-------|
| **Precision@K / hit rate** | Offline eval on held-out likes |
| **Intra-list diversity** | Genre / embedding spread on slate |
| **Coverage / serendipity** | Share of non-blockbuster titles in top-N |
| **Explanation coverage** | 100% of recs have ≥1 reason code |

### Engineering

* p95 recommend API (cache hit) < 150ms
* p95 recommend API (cold) < 800ms with catalog warm
* Zero critical OWASP issues in auth path
* Deployable via Docker in one command locally

---

## Phased roadmap

### Phase 0 — Foundation (current)
* Repo, constitution, product + architecture lock
* Monorepo skeleton, Docker Compose (API, Postgres+pgvector, Redis)

### Phase 1 — Catalog & identity
* Auth, users, title ingest (TMDb as **source**, not product)
* Normalized schema + embeddings pipeline (batch)

### Phase 2 — Taste & onboarding
* Swipe onboarding, signal store, taste profile builder
* First explainable ranking strategy (content + profile similarity)

### Phase 3 — Product surface
* Home “For you”, title detail, watchlist, search
* Redis slate cache, basic analytics events

### Phase 4 — Discovery quality
* MMR / diversity re-ranker
* Hidden-gems boost (quality vs popularity) — scored + explained + UI badge
* Exploration slots (soft quota via `REC_EXPLORATION_SLOTS`) — `discovery` reasons
* Offline eval harness

### Phase 5 — Production hardening
* CI/CD, rate limits, monitoring (Sentry optional), soft-launch checklist in `docs/DEPLOY.md`
* Host-managed DB backups + smoke path before invites
* Soft launch with real users

### Later (only with evidence)
* Collaborative signals, sequential models, multi-profile households, streaming deep-links, mobile

**Shipped recently:** post-watch rating on title detail; History with filters; Account taste summary chips.

---

## Feature decision template

Use this before adding anything:

1. **User problem?**  
2. **Business value?** (retention / trust / acquisition)  
3. **MVP or later?**  
4. **Simplest solution that works?**  
5. **Does it improve the core loop?**  

If it doesn’t improve the loop or remove friction, defer.

---

## Competitive positioning (honest)

| Approach | Them | Us |
|----------|------|-----|
| Letterboxd | Social + logging | Taste engine first; social later if ever |
| Netflix row | Opaque + popularity | Explainable + discovery-weighted |
| JustWatch | “Where to watch” | “What you’ll love” (where-to-watch is Phase later) |
| TMDb wrappers | Browse catalog | **Taste profile product** |

---

## Open product decisions (need founder input soon)

1. **Brand name** — CineTaste is placeholder.
2. **Movies only first vs Movies+TV day one** — recommendation: both in schema, TV in UI if ingest cost is low; else movies-first UI.
3. **Auth providers** — email first; Google OAuth after MVP if signup friction shows up.
4. **Where-to-watch** — shipped on title detail via TMDb/JustWatch (region selector; not deep-link into every app).
5. **Primary market language** — English catalog UX first unless you specify otherwise.

---

## Co-founder stance

We will **not** ship a pretty shell around TMDb search.

We will ship a product that:

* Learns who you are
* Shows you why
* Helps you find something you’d miss
* Feels effortless on a tired Tuesday night
