# Quality audit — November 2026

A full product, engineering and recommendation-system audit of CineTaste:
what was found, what was changed, how it was measured, and what is left.
Every claim below points at a test, a benchmark run or an observation of the
live site. Where something could not be verified, it says so.

**Scope covered:** repository (backend, frontend, docs, CI), the live site at
`cinetaste-rho.vercel.app` (guest surfaces only — no production account was
created or touched), the offline benchmarks (synthetic and MovieLens), and the
full local test stack (Postgres 16 + pgvector, Redis, Playwright).

---

## 1. Overall assessment

**Current condition.** CineTaste is a well-engineered, honest product. The
architecture is clean (pure ranking functions shared by the API, the tests and
the offline evaluation), the security work is unusually careful for a project
this size, and the documentation is candid about weaknesses — the MovieLens
"popularity beats us" result was published, not hidden. The UI is cohesive and
polished on desktop and in the core flows on mobile.

**Biggest strengths**

- Explainability is designed in: every reason is built from stored evidence,
  not generated text.
- One signal policy (`domain/taste_signals.py`) drives learning, feed
  exclusion, undo and explanations — no drift between call sites.
- Security fundamentals: rotating refresh tokens with reuse detection,
  in-memory access tokens, per-IP *and* per-account throttling, strict CSP,
  HSTS (verified live, see §5), hashed one-time tokens.
- Tests that pin product promises, migrations exercised by the integration
  suite, axe accessibility checks in E2E.

**Biggest weaknesses (before this work)**

- The recommender's *product* behaviour on real data had defects the tests did
  not catch: an auteur filter bubble (8 of the first 9 picks by one director),
  false "hidden gem" claims about some of the most-seen films ever made, and
  ungrammatical reasons.
- The offline benchmark did not measure the system production serves, and on
  MovieLens the production ranker reached only ~36% of the popularity
  baseline's recall.
- A concurrency bug could silently drop a user's latest rating from their
  taste profile.
- The catalog is built only from "most popular" and "most voted" lists, so the
  discovery promise had little to work with.
- Guest slates take 4–5 seconds to compute on the free-tier host.

**Production readiness.** Ready for a public beta at its current scale, with
known limits: free-tier CPU makes slate computation slow, the in-process cache
and rate limiter assume one process, and a deferred profile rebuild is lost if
the process dies at the wrong moment (documented, self-healing on the next
rating). No P0 issues were found.

---

## 2. Audit findings

Severity: **P0** critical security/data loss/outage · **P1** important
functional defect or serious production risk · **P2** significant product,
UX, recommendation or performance improvement · **P3** minor.

### P0

None found. Authentication, authorization (every `/me*` route derives the user
from the token), injection (bound parameters everywhere, LIKE escaping), CSP,
CORS and secret handling were reviewed; no critical issue surfaced.

### P1 — fixed

| # | Finding | Evidence | Fix |
|---|---|---|---|
| P1-1 | **Lost taste-profile updates.** Ratings rebuild the profile after the response. Two quick ratings start overlapping rebuilds; the one that read the event log first could commit last and overwrite the newer profile. The latest rating then vanished from recommendations until the next one. | `tests/integration/test_profile_concurrency.py::test_overlapping_rebuilds_keep_the_latest_rating` fails on the old code with *"the newest rating was lost"*. | Rebuilds for one user are serialised with a transaction-scoped Postgres advisory lock; profile writes are atomic upserts. The request-path version bump never waits for a rebuild. `application/taste_service.py` |
| P1-2 | **Colliding cache versions.** The For You cache is keyed by `profile.version`, bumped by a read-modify-write in Python. Two ratings at once read the same version and wrote the same next one. | `test_concurrent_bumps_both_count` fails on the old code. | `version = version + 1` in one SQL upsert. |
| P1-3 | **False "hidden gem" claims.** The gem rule keyed off TMDb `popularity` (this week's buzz), not reach. The live slate badged *Pulp Fiction* (29k votes) "not a chart-topper — a hidden gem", plus *The Prestige* and *Shutter Island*. An explanation that is false destroys the trust explanations exist to build. | Live guest slate, `POST /api/v1/guest/recommendations`. `test_a_widely_seen_classic_is_never_a_hidden_gem`. | Gems now need a high rating, at least 50 votes, a vote count in the bottom 35% of the candidate pool, and must not be trending. The message says only what was checked: "Rated ★8.1, yet far less seen than most films we considered — a hidden gem". `recommendation/pipeline.py` |
| P1-4 | **Auteur filter bubble.** Rating *Inception* and *The Dark Knight* highly produced a slate opening with eight Christopher Nolan films in nine cards. The per-genre cap cannot see it: the films spanned five genres. | Live guest slate. `test_one_director_cannot_take_over_the_slate` (old code: 8 of 20). | Per-director cap (2 per 20-card slate, proportional), enforced in MMR, exploration and backfill, relaxed only when the catalog cannot fill the slate. The director's best film still leads. |

### P2 — fixed

| # | Finding | Evidence | Fix |
|---|---|---|---|
| P2-1 | **The benchmark measured a different system.** Offline strategies ranked the *whole* catalog; production ranks a candidate pool (ANN neighbours + popular slice). MovieLens title statistics also counted the held-out ratings themselves. | Same users, old code: full catalog recall@20 0.024, production-faithful 0.030. Leakage: popular recall 0.072 → 0.070 when fixed. | New `production` strategy emulating candidate generation; title stats from training data only; `precision@k` reported; `--workers` (12 min → 6 min). `recommendation/evaluation.py`, `eval_datasets.py` |
| P2-2 | **Weak relevance on real ratings.** Root cause found: with only genres and a year for 84% of MovieLens films, **74% of titles share an identical feature snapshot** with another (largest group: 241 films). The ranker could not tell them apart and ties fell to catalog order — what looked like "novelty" was mostly noise. | Analysis in §4. | A **quality prior**: the pool percentile of a vote-count-shrunk average rating (weight 0.10). Production recall +43%, NDCG +61%, MRR +63%, diversity +18% on MovieLens (§4). |
| P2-3 | **The catalog is a popularity bubble.** Ingest walks only `popularity.desc` and `vote_count.desc`, so hidden gems barely exist in the catalog. | `application/catalog_ingest.py` | A third discover list: top-rated titles with 300–5,000 votes (150–3,000 for TV). **Takes effect after the Catalog workflow runs again.** |
| P2-4 | **Ungrammatical or uninformative reasons.** "Strong match on war actions", "comedys", "science fictions", raw codes ("In a language lane you already enjoy (en)"), "Leans into US cinema", "wartime" matched as a "time" theme. | Live slate; `tests/test_explanations.py` (new tests). | Proper genre nouns, tone adjectives, whole-word theme matching, country/language names instead of ISO codes, and no English/US origin reasons (they explain nothing in this catalog). `recommendation/explanations.py` |
| P2-5 | **Mobile landing overflowed.** The hero glow bled 30% past the viewport; an iPhone-sized screen laid the first page out 507 px wide and rendered it zoomed out. | Live audit (`innerWidth` 507 on a 390 px device). E2E `Landing: a phone never has to scroll sideways`. | `overflow-x: clip` on the landing root (keeps the sticky header working). |
| P2-6 | **Guests could not say "I've seen it".** Guest picks offered only "Want to watch" and "Not for me", so a film someone loved could only be recorded as a rejection — which was replayed into their account at sign-up. | Live guest flow. E2E `Guest: a pick you've already seen can be rated`. | Guest results reuse the For You actions: *Seen it* → rate. Ratings go into the guest draft, sharpen *More picks*, and carry into onboarding. |
| P2-7 | **Shared links lost on sign-in.** A signed-out visitor opening `/titles/:id` was sent to `/login` and then to the home page. | Live audit. E2E `A shared title link survives signing in`. | The destination travels in router state and is validated (same-app paths only, no auth pages; open-redirect cases unit-tested in `returnTo.test.ts`). |
| P2-8 | **One-time tokens usable twice.** A password-reset link submitted twice at once changed the password twice; same pattern for email verification. Low exploitability (needs the token), but "one-time" was not true. | `test_a_reset_link_works_once_even_when_submitted_twice_at_once` returned `[204, 204]` on the old code. | Row lock on the token (`SELECT … FOR UPDATE`). |
| P2-9 | **Slow guest slates.** Live `POST /guest/recommendations` took 3.9–5.2 s to first byte; cached guest cards 0.3 s. Profiling found `feature_family` (≈7,400 calls per slate, scanning every prefix) at ~45% of ranking time. | Local profile on a 1,200-title catalog. | Family read straight off the key, verified equivalent to the scan by a test. Guest slate 90–114 ms → 64–72 ms locally (≈35%). On a 0.1-CPU instance that is roughly a second. **Not re-measured in production.** |

### P3 — documented, not changed

| # | Finding | Recommendation |
|---|---|---|
| P3-1 | Every first visit sends `POST /auth/refresh` and logs a 401 in the console. Deliberate (Safari can drop the session hint while the cookie lives), but it is noise and an extra request to a sleeping API. | Answer "no session" with a 204 instead of a 401, or keep and accept. |
| P3-2 | `HEAD /api/v1/health` answers 405 (FastAPI GET routes). The repo's uptime workflow uses GET, but third-party monitors often use HEAD. | Register `HEAD` on health/ready. |
| P3-3 | Most routes leave the document title at "CineTaste" (only landing and `/try` set one) — tabs, history and screen readers (WCAG 2.4.2). | A `<Helmet><title>` per page. |
| P3-4 | Footer and inline text links are 17–18 px tall (WCAG 2.5.8 recommends 24 px targets; inline links are exempt, footer links are borderline). | Pad footer links. |
| P3-5 | The per-account login throttle can be used to lock a known user out (10 failures per 15 min). SECURITY.md said an active user is "never" locked out; that holds only without an attacker. | Key the lock on (account, IP prefix) with a higher account-wide ceiling, or CAPTCHA after N failures. The doc is corrected. |
| P3-6 | The in-process store's tracked-slate index never expires (grows with users on single-process deploys). | Bound it like the value store. |
| P3-7 | pgvector values arrive in text format; parsing ~380 vectors is ~30% of candidate hydration. | Register the binary asyncpg codec (needs care: bind parameters change type). |
| P3-8 | The landing shows two CTA pairs ("Try it now"/"I already have an account", then "Start free"/"Create an account"). | One clear secondary action. |
| P3-9 | Four backend files are not `ruff format`-clean; CI only runs `ruff check`. | Add `ruff format --check` once reformatted. |

---

## 3. Implemented improvements

| Area | Change | Files |
|---|---|---|
| Data consistency | Serialised profile rebuilds (advisory lock), atomic version bump and profile upsert; import/clear take the lock first to avoid a deadlock with a background rebuild | `backend/app/application/taste_service.py` |
| Diversity | Per-director cap in MMR, exploration and backfill; multi-constraint MMR | `backend/app/recommendation/pipeline.py` |
| Explanations | Gems by reach with truthful wording; grammar; whole-word themes; place names instead of codes | `pipeline.py`, `explanations.py`, `frontend/src/pages/LandingPage.tsx` |
| Relevance | Quality prior (`RankingWeights.quality = 0.10`) | `pipeline.py` |
| Performance | O(1) feature-family lookup | `backend/app/recommendation/embeddings.py` |
| Evaluation | Production-faithful strategy, leakage-free MovieLens stats, precision column, parallel workers, quality ablation | `evaluation.py`, `eval_datasets.py`, `scripts/evaluate_recommender.py` |
| Catalog | Acclaimed-but-less-seen discover list; TMDb client `vote_count.lte` | `application/catalog_ingest.py`, `infrastructure/tmdb/client.py` |
| Security | Row locks on one-time reset and verification tokens | `application/auth_service.py` |
| Frontend | Mobile landing overflow; deep-link return after sign-in; *Seen it* for guests | `App.tsx`, `LoginPage.tsx`, `features/auth/returnTo.ts`, `GuestPage.tsx`, `PickActions.tsx`, `styles/landing.css` |

Each P1/P2 fix has a regression test that was run against the old code and
failed there (the director cap, the race, the token lock and the three E2E
tests were all checked this way).

---

## 4. Recommendation engine

### Why the production ranker lost to popularity on MovieLens

Investigated, in order:

1. **Leakage — real but small.** Title popularity and ratings were computed
   from *all* ratings, including the held-out ones being predicted. Fixed;
   popular recall@20 moved 0.072 → 0.070. Not the explanation.
2. **The benchmark did not test production.** Strategies ranked all 9.7k
   films; production ranks ~370 candidates. Faithful emulation raised the old
   ranker's recall from 0.024 to 0.030 on the same users.
3. **The dataset cannot represent CineTaste's metadata.** MovieLens has no
   director, cast, writer, keyword or synopsis data; only 16% of films have a
   user tag; genre lists are alphabetical in 99% of rows, so the "primary
   genre" used by the diversity cap is arbitrary there.
4. **Ties, not taste, decided most picks.** 74% of the catalog shares an
   identical feature snapshot with at least one other film (largest group:
   241). Among equally scored films the old ranker picked by catalog order —
   mostly obscure ones, which read as "novelty" but carried no signal.
5. **Exposure bias in the data.** MovieLens asks people to rate popular films;
   the top 5% of films hold 43% of all ratings. A popularity baseline is
   structurally favoured on "what will this user rate next".

Points 3–5 mean MovieLens is a floor for a content recommender, not a verdict
on it. The fix that helps *both* the benchmark and the product is a
tie-breaker that prefers well-rated films (quality) rather than famous ones
(popularity).

### Results — same 193 MovieLens users, slate of 20, leakage-free protocol

| strategy | recall | precision | NDCG | hit rate | MRR | diversity | genre variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|---|
| random | 0.003 | 0.003 | 0.003 | 0.062 | 0.023 | 0.728 | 0.360 | 0.504 | 0.331 |
| popular | 0.070 | 0.047 | 0.067 | 0.415 | 0.168 | 0.811 | 0.285 | 0.002 | 0.014 |
| **production, before** | 0.030 | 0.019 | 0.028 | 0.259 | 0.078 | 0.532 | 0.217 | 0.273 | 0.081 |
| **production, after** | **0.043** | **0.033** | **0.045** | **0.352** | **0.127** | **0.628** | **0.232** | 0.139 | 0.057 |
| change | +43% | +74% | +61% | +36% | +63% | +18% | +7% | −49% | −30% |

The previously published production row (0.025 / 0.028 / 0.254 / 0.085) was
reproduced exactly before any change; it used the old protocol.

Synthetic benchmark (800 titles, 200 users) — the control, where taste is
learnable from metadata:

| strategy | recall | NDCG | hit rate | MRR | diversity | novelty | coverage |
|---|---|---|---|---|---|---|---|
| production, before | 0.093 | 0.047 | 0.265 | 0.048 | 0.749 | 0.549 | 0.420 |
| production, after | 0.097 | 0.051 | 0.255 | 0.052 | 0.760 | 0.582 | 0.398 |

**Trade-offs, stated plainly**

- Novelty on MovieLens halves (0.273 → 0.139) but stays 70× the popularity
  baseline; coverage stays 4× it. Much of the old novelty was the tie noise
  described above.
- The quality prior was chosen over a popularity prior on the evidence (same
  users, current code): at equal novelty (~0.155) it gives 42% more recall
  (0.037 vs 0.026); a popularity prior needs weight 0.08 to approach its NDCG
  (0.038 vs 0.039), by which point novelty has fallen to 0.091. That stronger
  popularity prior does edge it on hit rate (0.332 vs 0.316) and MRR — at the
  cost of recommending noticeably more of the charts.
- The director cap costs ~8% recall on the synthetic benchmark (whose users
  are *generated* from director clusters, so it rewards exactly the bubble the
  cap prevents). It is a deliberate product choice; it has no effect on
  MovieLens, which has no director data.
- Sampling noise at 193 users is real; compare rows on the same users only.

### Remaining limitations

- **No benchmark with real CineTaste metadata yet.** The planned experiment —
  enrich MovieLens with TMDb directors, cast, keywords and synopses via
  `links.csv` — needs a TMDb v4 *read access token*; the credential provided
  to this session was rejected by TMDb ("Invalid API key"), so it did not run.
- Offline metrics reward re-finding what someone already rated; they cannot
  value a pleasant surprise. `recommendation_impressions` is in place to
  measure saves and ratings per shown pick once there is traffic.
- The hashed content vector remains the weakest channel (dense-only recall
  0.013 on MovieLens). A learned text embedding is the next content upgrade.
- The new gem discover list only matters after the catalog is re-ingested.

---

## 5. Quality assurance

All runs below were executed in this audit; counts are observed, not copied.

| Suite | Before | After |
|---|---|---|
| Backend unit (`pytest -m "not integration"`) | 274 passed | 287 passed |
| Backend integration, no Redis (`INTEGRATION_REQUIRED=1`) | 33 passed | 38 passed |
| Backend integration, with Redis | 33 passed | 38 passed |
| Backend coverage (CI gate ≥ 80%, per-package floors) | — | 86.7%, all packages pass |
| `ruff check` | pass | pass |
| Frontend unit (vitest, with coverage) | 94 passed | 99 passed |
| Frontend production build (`tsc -b && vite build`) | pass | pass |
| Playwright E2E + axe | 60 passed | 63 passed |

No test was skipped or disabled. Three mock-based unit tests of the old
version-bump code were replaced by real-Postgres integration tests, because
the behaviour now lives in SQL.

**Browser verification (live site, guest only).** Landing, `/try`, rating,
skipping, results, save, pass, *More picks* path, direct navigation to every
protected route, 404, legal pages, reset/verify pages without tokens — at
1440×900, iPad Mini and iPhone 13. Observed: LCP 0.6–1.3 s, one JS bundle
(103 kB transferred), no page errors, two expected 401s per load (P3-1), the
507 px mobile layout (P2-5), protected deep links landing on `/` after sign-in
(P2-7). Response headers verified: HSTS on the API
(`max-age=31536000; includeSubDomains`) and on the SPA (with `preload`), CSP,
`X-Frame-Options`, `nosniff`. After the changes, the same flows were checked
locally against the production build with the E2E API mocks (mobile
screenshots of the landing and guest results).

**Not verified, and why**

- *Authenticated and destructive journeys on production* (registration,
  login, refresh, logout, onboarding, For You, detail, watchlist, history,
  undo, import/export, deletion) — deliberately not run against production.
  They are covered locally by the integration suite (real Postgres) and the
  E2E suite (mocked API).
- *Password recovery and email verification delivery* — needs the production
  mail provider; flows are covered by integration tests with dev tokens.
- *Where to watch with live data* — needs TMDb; the soft-fail path is tested.
- *Production latency after the changes* — not deployed. The local profile is
  the evidence; re-measure guest slates after deploy.
- *Real-metadata benchmark* — see §4.

**Remaining risks:** the deferred-rebuild loss on process death (documented,
self-healing); free-tier CPU latency; single-process in-memory cache and
limits if `WEB_CONCURRENCY` is raised without Redis.

---

## 6. Roadmap — the next five improvements

1. **Measure online, not just offline.** *Done (follow-up):*
   `python -m app.scripts.engagement_report` and the **Engagement report**
   workflow report save / already-loved / pass rates per position and reason
   code from `recommendation_impressions` + `interaction_events`
   ([EVALUATION.md](EVALUATION.md#online-what-people-do-with-their-picks)).
   Next: run it before and after this release's deploy.
2. **Re-ingest the catalog and benchmark on real metadata.** Run the Catalog
   workflow (now with the gem list), then enrich MovieLens via TMDb to measure
   the director, cast and keyword channels the production catalog relies on.
3. **Make slates fast on the free tier.** *Follow-up, measured:* loading
   candidates as plain columns and full rows only for the picks was tried and
   reverted — interleaved A/B end to end, 68 → 66 ms, within noise; the
   saving is eaten by the extra hydration query. The binary pgvector codec
   would save ~17 ms of vector parsing but needs a custom column type across
   ingest, ANN and recompute. Local CPU (~60 ms) cannot explain production's
   4–5 s, so slates now report `Server-Timing` and a `slate_timing` log line
   splitting database time and round trips (9 per guest slate) from ranking
   (RUNBOOK §4b). Next: read production numbers, then fix what they show —
   most likely API↔database region and Neon cold starts.
4. **A learned text embedding for the dense channel** (same 384 dimensions,
   batch re-embed), evaluated against the hashed vector before it ships. It is
   the weakest channel today and the only one that can link "heist" and
   "robbery".
5. **An explainable collaborative signal** once there are enough users:
   item-to-item co-occurrence from `interaction_events` as an extra candidate
   source, explained as "people who loved *X* also loved this" — relevance for
   cold titles without giving up the explainability promise.
