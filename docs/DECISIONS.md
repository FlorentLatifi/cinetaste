# Decision log

Lightweight ADR-style log. Newest first.

---

## 2026-11-22 — Low-rating penalty on (0.3)

### Decision
`RankingWeights.low_rating` 0 → **0.3**. `low_rating_penalty` is unchanged: 0
at ★6.5 and above, rising linearly to 1 at ★5.5, and only for titles with
enough votes to trust the rating (the hidden-gem vote floor).

### Rationale
After the quality prior shipped, a Nolan/Villeneuve fan's live slate still
had *The Matrix Resurrections* (★6.3) second, on director and tone overlap.
Swept 0.1 / 0.2 / 0.3 / 0.5 on 581 MovieLens users with TMDb metadata, paired
against the production of the same day (docs/EVALUATION.md, "Weight
changes"): recall and NDCG unchanged at every weight, like/dislike AUC
+0.048 at 0.2–0.3 (0.705 → 0.753), novelty −0.008. 0.3 rather than 0.2
because held-out dislikes reach slates no more often than before (+0.0003 vs
+0.0009) and, by its live score, it moves the example to about fifth place
(an estimate; MMR decides the exact spot).

### Rejected alternatives
* **0.5** — +0.004 more AUC for more coverage and novelty loss; within noise.
* **A hard rating floor** — would also drop titles a user's taste strongly
  fits; a penalty lets strong taste evidence still win.

### Status
Active. Coverage falls 0.309 → 0.274 offline. Watch `engagement_report`
together with the quality-prior change.

---

## 2026-11-22 — Quality prior on (0.05); language and TV caps sized to the person

### Decision
* `RankingWeights.quality` 0 → **0.05**: a vote-count-shrunk rating
  percentile (`quality_percentiles`) joins the score.
* **Language cap**: each original language may take the larger of 40% of the
  slate (`language_cap_for`) and its share of the user's positive taste. No
  language signal (cold start) → no language cap.
* **TV cap**: at most 20% of a slate (`tv_cap_for`), or the user's share of
  positive TV taste if larger. Guest slates are films only (`max_tv=0`) unless
  films cannot fill them.
* The shares come from the profile's **taste mix** (`recommendation/mix.py`):
  effective signal weight per language and format, computed in
  `build_profile` and stored at `TasteProfile.features["__taste_mix__"]`.
  Profiles built before it fall back to the positive `lang:` / `media:`
  features until their next rebuild.
* Both caps join `_SlateQuotas`; backfill relaxes language right after genre
  and TV last.
* Reasons rotate themes: a theme word named on 3 cards of a slate is left out
  of later cards that share another real theme (`THEME_REPEAT_LIMIT`).

### Rationale
Seen on the live site (2026-11-22): a Nolan/Villeneuve fan's #1 pick was *The
Matrix Resurrections* (★6.3) on director overlap alone; four non-English
favourites (one Ghibli film among Korean, French and Brazilian ones) produced
a guest slate that was 11/20 Japanese, mostly anime, including two TV series
on a page that promises films; "dystopia" explained 12 of 20 cards.

Offline (581 MovieLens users with TMDb metadata, paired): recall@20 0.052 →
0.062, like/dislike AUC 0.657 → 0.705, novelty 0.390 → 0.334; all launch
checks stay at 0. The quality prior is the whole accuracy gain; the language
and TV caps cost nothing measurable (docs/EVALUATION.md, "Weight changes").

Why the mix is computed rather than read from the `lang:` features: those
are clamped per key (`FEATURE_CAP` 4.5) before family normalisation, so 30
English favourites and 3 Korean ones read as 53% English. The mix says 91%.

### Rejected alternatives
* **A fixed per-language cap for everyone** — would push a monolingual
  English rater onto 12 non-English cards per slate.
* **`quality=0.1`** — more recall, but gives back 60% of the novelty the
  percentile gems bought; 0.05 is the measured halfway point.
* **Filtering TV out of For You entirely** — someone who rates series should
  get series; the cap follows their share.

### Status
Active. Watch `engagement_report` after deploy: positive-action rate up,
negative-action rate not up. Existing profiles pick up the stored mix on
their next rating; until then the fallback applies.

---

## 2026-11-21 — Catalog ingest writes a batch per round trip, not a title

### Decision
`CatalogIngestService` writes each batch of 25 titles with a fixed set of
statements: one `SELECT` each for the batch's titles, people and keywords
(`= ANY($1)`, so asyncpg reuses the prepared statement), one flush inserting the
new people, keywords and titles, one executemany `UPDATE` for stored titles, one
`DELETE` per join table and one flush inserting the join rows. That is 11
statements for a batch of new titles and 12 for stored ones, whatever the batch
size. Lookup rules are unchanged and run in memory (genres and keywords by TMDb
id, then by name; people by id; the first name seen wins). If the database
rejects one title's row, the batch is redone one savepoint per title, so that
title alone fails, as before.

### Rationale
The Catalog workflow runs on a GitHub runner, ~150 ms from Neon, so every
statement is a round trip. Writing title by title took 57 statements for a
stored title and up to 98 for a new one: run #8 measured 8.5 s per title, and
`popular` (~3,400 titles) needed about 8 hours against a 150-minute job limit.
`tools/profile_catalog_ingest.py` reproduces this locally through a proxy that
adds latency: at 150 ms the old code took 9.4 s per stored title and 11.5 s per
new one. The new code takes 0.15 s and 0.11 s. On 420 real TMDb payloads the
old and new code store identical rows in every catalog table.
`tests/integration/test_catalog_ingest.py` pins the statement count.

Stored titles are updated through one executemany instead of the unit of work
because `Base` sets `eager_defaults=True`. That `RETURNING updated_at` forces a
separate `UPDATE` per row, and it stays, because async code must not lazy-load
`updated_at`.

### Also fixed
A batch redone after a lost connection ran inside the `except` block, so any
error in it carried the lost connection as `__context__`. `_connection_is_lost`
follows that chain, so one malformed payload in the redone batch stopped the
run as a second lost connection.

### Status
Active. Confirm with the next `popular` run's `ingest_progress` lines.

---

## 2026-11-21 — Penalise disliked features harder (`sparse_negative` 0.12 → 0.5)

### Decision
`RankingWeights.sparse_negative` defaults to 0.5 (was 0.12): the score
subtracts more for overlap with features the user's dislikes outweigh.

### Rationale
On 581 MovieLens users with TMDb metadata, the score separates held-out liked
from held-out disliked titles better — like/dislike AUC +0.012 [+0.002,
+0.024] on the current slate rules, +0.010 [+0.003, +0.019] on the rules
before the caps, replicated at k=10 and on the full 9.6k catalog — with no
measurable accuracy cost in any setting. The sweep rises from 0 to 0.5 and
falls after it: beyond 0.5 liked titles start being pushed down too, because
likes and dislikes share genres, decades and cast. The whole range moves AUC by
under 0.02 — MovieLens dislikes are films the user chose to watch, so most of
their features cancel against the likes. A "Not for me" on a recommended card
is a cleaner negative, which is why the online check matters more than the
offline one here.

### Watch
At k=20, held-out dislikes reach the slate very slightly more often (+0.0008,
interval touching zero). The negative-action rate in `engagement_report`
should not rise after deploy; if it does, revert to 0.12.

### Status
Active.

---

## 2026-11-21 — Cap directors and franchises; hidden gems by catalog percentile

### Decision
* **Slate caps**, shared by MMR, exploration and backfill (`_SlateQuotas` in
  `select_slate`): primary genre 40% of the slate, **director 10%** (2 in 20),
  **franchise (TMDb collection) 5%** (1 in 20). When a slate cannot be filled,
  backfill relaxes the genre cap first, then director, then franchise.
* **Hidden gem** = ★7.2+, 50+ votes, and below the 40th *catalog* percentile
  on both popularity and vote count. Percentiles live in
  `Title.extra["catalog_percentiles"]`, refreshed by one SQL statement at the
  end of every ingest and re-embed (`reembed_catalog --percentiles-only`
  backfills on its own).
* A quality prior exists as a knob (`RankingWeights.quality`) and is **off**
  (turned on at 0.05 on 2026-11-22, see above).

### Rationale
Both were soft-launch checks that `master` failed, measured on 581 real
MovieLens users with TMDb metadata (docs/EVALUATION.md):

* In 49.6% of 20-card slates one director held 3+ titles; the genre cap could
  not see it (a guest slate on the live site once opened with eight Nolan films
  in nine cards). With the caps: 0%.
* 96% of "hidden gem" labels went to the catalog's 10% most-voted films. The
  rule tested TMDb `popularity < 40`, but TMDb's popularity is a trending score
  whose scale shrank: 99.8% of titles are below 40, so Pulp Fiction and Forrest
  Gump were "hidden gems". Percentiles mean the same thing whatever TMDb's
  scale does. With them: 0%. A false reason is worse than none.

The cost is offline recall@20 0.069 → 0.052 (paired Δ −0.017 [−0.025, −0.009]).
About half is the old gem bonus going away — it fired on nearly every
well-rated film, so it was a quality prior in disguise — and about a third is
the new bonus promoting genuinely little-known films, which offline recall
counts as a miss by construction. The caps alone cost −0.002 (interval spans
zero). Shipped anyway, because the offline metric is the one most likely to
overstate the loss; `engagement_report` measures the real one.

### Rejected alternatives
* **Rescaling the raw popularity thresholds** — breaks again the next time
  TMDb changes its formula.
* **The quality prior on by default** (`quality=0.1` recovers master's recall
  and beats it on like/dislike AUC, at 60% of the novelty gain) — left as the
  owner's call, to be decided on online numbers.

### Origin
The November 9 quality audit (`florent/fervent-keller-0l0j9b`, 44fd168),
re-done in `florent/ecstatic-hypatia-gexedy` and landed by PR #61 on top of
the `candidate_channels` → `blend_scores` → `select_slate` split.

### Status
Active. Production needs one percentile backfill after deploy, and franchises
reach existing titles on the next ingest (docs/DEPLOY.md).

---

## 2026-11-21 — The benchmark measures what production serves, on real metadata

### Decision
The offline benchmark joins MovieLens ratings to TMDb details (`links.csv`),
built into features by the same mapping ingest uses (a parity test runs the
real ingest code on the same payloads). The `production` strategy ranks the
candidate pool the API retrieves, with exploration slots. The in-domain
popularity baseline counts training ratings only. Strategies are compared with
paired bootstrap intervals. The TMDb cache lives outside the repository.

### Rationale
MovieLens alone has genres, tags and a year, so directors, cast, keywords,
tones and synopses — what production ranks on — were never exercised, and
popularity won. Strategies also ranked the whole catalog, which production
never does, and held-out ratings leaked into the popularity baseline. Weights
were being chosen against a different system.

### Status
Active. `python -m app.scripts.evaluate_recommender --dataset movielens-tmdb`.

---

## 2026-11-21 — The engagement report reads a local copy, never production

### Decision
`engagement_report` runs with `default_transaction_read_only=on` and refuses
any database host that is not local, with no override. Production data is
analysed from a restored backup. The GitHub Actions workflow on
`florent/fervent-keller-0l0j9b` that ran a similar report against the
production `DATABASE_URL` was not adopted.

### Rationale
Analysis tooling should not hold production credentials or be one flag away
from reading live user data. Restoring a backup is a few minutes of friction
for a report that is read every few weeks.

### Revisit when
Reports are wanted on a schedule: then a dedicated read-only database role
and a workflow that can use nothing else.

### Status
Active.

---

## 2026-11-21 — Profile rebuilds are serialised per user

### Decision
`recompute_profile` takes a transaction-scoped advisory lock keyed by the user
and writes the profile as an upsert with `version = version + 1` in SQL. The
request-path version bump is the same kind of upsert and never takes the lock.
Snapshot import and clear take the lock before touching the row.

### Rationale
Deferred rebuilds made overlapping rebuilds normal for anyone rating quickly.
The rebuild that read the log first could commit last and overwrite the newer
profile, so the latest rating vanished from recommendations until the next
one; two first ratings at once could also both insert the row. Row locks were
rejected: the row may not exist yet, and `SELECT … FOR UPDATE` in the rebuild
would make the request's bump wait for it, undoing the reason rebuilds were
deferred. Import and clear lock first because the opposite order (row, then
lock) deadlocks against a background rebuild holding the lock.

### Origin
Found in the November 9 audit (`florent/fervent-keller-0l0j9b`, 0e5bcf8);
landed in PR #59 with the races reproduced in `test_profile_concurrency.py`.

### Status
Active.

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
