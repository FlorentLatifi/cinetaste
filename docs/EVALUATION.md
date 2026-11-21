# Evaluating the recommender

Anyone can claim a recommender works. This is how CineTaste measures it, what
the numbers are today, and where they are weak.

```bash
cd backend
python -m app.scripts.evaluate_recommender                   # synthetic, no downloads
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb \
    --path ~/.cache/cinetaste/eval/ml-latest-small -k 20 --users 0   # real ratings + real metadata
python -m app.scripts.engagement_report                      # online: what users did with picks
```

The scripts read the backend settings (a local `.env` is enough); no database
is contacted except by `engagement_report`. Setup for the real-data run is in
[Reproducing](#reproducing).

## Protocol

For every user:

1. Split their rated titles into **train** and **test**: held-out titles they
   rated positively and, where the data has them, held-out titles they disliked.
2. Build the taste profile from the train events with the **production code
   path** — `effective_title_signals` → `build_profile`, the same functions the
   API calls. An evaluation that re-implements the model measures the
   re-implementation.
3. Rank, excluding everything in train: the whole catalog, or — for
   `production` — only the candidate pool the API retrieves (nearest
   neighbours of the taste vector plus the popular slice, see
   `recommendation_service._load_candidates`).
4. Score the slate against the held-out titles.

Every strategy runs through the same protocol on the same users, so the
comparison is fair; differences between strategies are reported as **paired
deltas with 95% bootstrap intervals** (`paired_deltas`), because user-to-user
noise is large at a few hundred users.

| Strategy | What it is |
|---|---|
| `random` | Shuffle the catalog. The floor. |
| `popular` | Most popular unseen titles by TMDb `popularity` — the popularity production can see. |
| `popular_ml` | Most-rated titles *in the training split* of MovieLens: the in-domain popularity baseline, leak-free. (movielens-tmdb only) |
| `dense_only` | Content-vector similarity only (no sparse features). |
| `sparse_only` | Interpretable feature overlap only (no vector). |
| `hybrid_no_diversity` | Production scoring, diversity controls off. |
| `hybrid` | Production scoring + MMR λ=0.8 + per-genre cap, over the whole catalog, no exploration. |
| `hybrid_with_exploration` | `hybrid` plus 3 exploration slots. |
| `hybrid_plus_popularity` | `hybrid` with `warm_popularity=0.2`. |
| **`production`** | **What the API serves**: the production candidate pool, MMR λ=0.8, genre cap, 3 exploration slots (`Settings.rec_*`). |

Before November 2026 the table below called `hybrid` "production". It never
was: production also runs exploration and ranks a candidate pool, not the
whole catalog.

## Metrics

**Accuracy** — recall@k (share of held-out titles that made the slate),
precision@k, NDCG@k (credit for ranking hits near the top, graded by rating
where available), hit-rate@k, MRR.

**Beyond accuracy** — a recommender that returns the ten most popular titles
scores acceptably on accuracy and is useless as a product:

* `diversity` — 1 − mean pairwise cosine within a slate (higher = less repetitive).
* `genre_variety` — distinct primary genres ÷ slate size.
* `novelty` — mean popularity percentile (0 = blockbusters only, 1 = obscure).
* `coverage` — share of the catalog that ever gets recommended.

**Negative feedback** — does the ranker respect what users dislike?

* `dislike_recall@k` — share of a user's held-out *disliked* titles (≤ 2★) that
  made the slate. Lower is better; compare with recall@k of the liked ones.
* `like_rank_pct` / `dislike_rank_pct` — where the score places held-out liked /
  disliked titles among all unseen candidates (0 = top, 1 = bottom).
* `like_dislike_auc` — probability that a held-out liked title outscores a
  held-out disliked one. 0.5 = the score cannot tell them apart.

**Launch checks, measured offline**

* `director_max` — most titles one director holds in a slate;
  `director_over_limit` — share of slates where that exceeds 2 (the S2 check).
* `gem_labels` — "Hidden gem" labels per slate; `gem_fame_pct` — mean
  vote-count percentile of labelled titles (1 = the most-voted film in the
  catalog); `gem_famous_share` — share of labels on the catalog's most-voted
  10% (a famous film called a hidden gem).

## Results: real ratings with real metadata (November 2026)

MovieLens ratings joined to TMDb through `links.csv`; title features built by
`signal_inputs_from_payload` → `build_title_signals`, which a parity test
(`tests/test_tmdb_eval_cache.py`) holds to exactly what ingest stores. So for
the first time the offline numbers exercise directors, writers, cast,
keywords, tones, synopsis words, language and country — the features
production ranks on.

| Dataset | |
|---|---|
| MovieLens ml-latest-small movies with a TMDb id | 9,733 |
| … with TMDb details (113 ids no longer exist on TMDb) | 9,620 |
| Ratings kept (of 100,836) | 100,515 |
| Catalog (candidates): the most-voted titles, production-sized | 4,500 |
| Users evaluated (≥ 1 held-out like in the catalog) | 581 |
| Held-out likes (≥ 4★) / dislikes (≤ 2★, from 311 users) | 8,019 / 1,930 |

Temporal split per user (latest 20% held out), slate of 20. TMDb metadata
fetched 2026-11-21.

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.003 | 0.003 | 0.055 | 0.009 | 0.945 | 0.442 | 0.509 | 0.918 |
| popular (TMDb) | 0.035 | 0.029 | 0.275 | 0.059 | 0.911 | 0.341 | 0.003 | 0.013 |
| popular_ml | **0.093** | **0.082** | 0.444 | **0.178** | 0.927 | 0.425 | 0.064 | 0.034 |
| dense_only | 0.028 | 0.022 | 0.210 | 0.046 | 0.833 | 0.294 | 0.432 | 0.190 |
| sparse_only | 0.075 | 0.065 | 0.449 | 0.138 | 0.898 | 0.354 | 0.232 | 0.269 |
| hybrid_no_diversity | 0.070 | 0.063 | 0.422 | 0.128 | 0.888 | 0.341 | 0.237 | 0.235 |
| hybrid | 0.070 | 0.062 | 0.451 | 0.136 | 0.910 | 0.372 | 0.229 | 0.239 |
| hybrid_with_exploration | 0.069 | 0.060 | 0.439 | 0.133 | 0.912 | 0.490 | 0.231 | 0.235 |
| hybrid_plus_popularity | 0.084 | 0.074 | **0.504** | 0.153 | 0.916 | 0.380 | 0.077 | 0.156 |
| **production** | **0.069** | **0.061** | **0.448** | **0.133** | **0.898** | **0.470** | **0.211** | **0.235** |

| strategy | dislike_recall@20 | like_rank_pct | dislike_rank_pct | like_dislike_auc | director_max | director_over_limit | gem_labels | gem_fame_pct | gem_famous_share |
|---|---|---|---|---|---|---|---|---|---|
| random | 0.003 | – | – | – | 1.18 | 0.003 | – | – | – |
| popular (TMDb) | 0.022 | 0.272 | 0.342 | 0.568 | 2.95 | 0.957 | – | – | – |
| popular_ml | 0.028 | 0.146 | 0.204 | 0.593 | 2.94 | 0.788 | – | – | – |
| sparse_only | 0.014 | 0.203 | 0.386 | 0.702 | 4.66 | 0.898 | 3.0 | 0.938 | 0.873 |
| hybrid | 0.010 | 0.201 | 0.390 | 0.703 | 3.40 | 0.640 | 3.0 | 0.941 | 0.881 |
| **production** | **0.012** | **0.201** | **0.390** | **0.703** | **2.72** | **0.496** | **3.0** | **0.970** | **0.962** |

### What it says

**1. With real metadata the recommender beats the popularity production can
see — but not in-domain popularity.** Production's recall is 2× the TMDb
popularity baseline (0.069 vs 0.035; paired Δ −0.033 [−0.045, −0.021] for
`popular`). On the MovieLens-only features of September it was a third of it.
The most-rated-in-MovieLens baseline still wins on recall (Δ +0.024 [+0.010,
+0.039]) and MRR, ties on hit rate, and gets there by recommending the charts:
novelty 0.064 vs 0.211, coverage 3% vs 24% of the catalog. Part of that win is
the protocol, not taste: MovieLens users rate what they watch, and what this
population watched next is what was popular with this population — an
advantage no content model can have.

**2. Popularity still buys accuracy at a steep price.** Every +0.05 of
`warm_popularity` adds about +0.005 recall and costs about 0.05 novelty
(table below): +0.2 lifts recall 22% and cuts novelty 68%. The September
decision — keep it off by default in a discovery product — still holds.

**3. Candidate retrieval and diversity cost nothing measurable.** Ranking
production's ANN+popular pool instead of the whole catalog: Δ recall +0.0003
[−0.0068, +0.0076] (`hybrid_with_exploration`, the same configuration over the
whole catalog, vs `production`). MMR and the genre cap: 0.070 recall with or
without them. Exploration slots raise genre variety from 0.37 to 0.47–0.49 at
no measurable recall cost.

**4. Dislikes are respected, mostly by the content itself.** A held-out
disliked title reaches the slate at a sixth of the rate of a liked one (0.012
vs 0.069), and the score separates likes from dislikes with AUC 0.70 (TMDb
popularity: 0.57). Most of that does **not** come from the negative channel:
`sparse_negative` from 0 to 0.12 moves AUC by only 0.006. See the sweep.

**5. "Hidden gem" labels go to famous films — a production bug.** 96% of
production's gem labels land on the catalog's 10% most-voted titles; the mean
labelled title sits at the 97th fame percentile. `gem_boost` requires TMDb
`popularity < 40`, but TMDb's popularity is a trending score whose scale has
shrunk: in this data the median is 2.5, the 99th percentile 20, and **99.8%
of titles are below 40**. The condition never bites, so "hidden gem" means
"★7.2+ with 50 votes": 1,392 of the 4,500 catalog titles and 245 of the 350
films with ≥ 10k votes — Interstellar, Inception, The Dark Knight,
Shawshank, Pulp Fiction. Production stores the same TMDb popularity at
ingest, so the live launch check "no famous title labelled Hidden gem" is
expected to fail for this reason.

**6. The gem bonus is secretly the quality prior.** Because it fires on
almost every well-rated film, `hidden_gem=0` costs 13% of recall (Δ −0.0089
[−0.0159, −0.0011]) and drops like/dislike AUC from 0.70 to 0.64 — the largest
AUC effect of any weight. Fixing the label (point 5) also removes this prior;
see the branch measurement below.

**7. Half of all slates break the director check (S2).** In 49.6% of
production slates one director holds 3+ of the 20 titles (mean max 2.7);
without MMR and the genre cap it would be 87%. There is no per-director cap
in `master`.

### Weight sweeps (production configuration)

Each row changes one weight of `production`; deltas are paired against it.

| variant | recall@20 | ndcg@20 | novelty | coverage | like_dislike_auc | like_rank_pct | dislike_rank_pct | director_over_limit |
|---|---|---|---|---|---|---|---|---|
| sparse_negative=0 | 0.068 | 0.060 | 0.211 | 0.230 | 0.697 | 0.200 | 0.384 | 0.496 |
| sparse_negative=0.06 | 0.068 | 0.060 | 0.211 | 0.232 | 0.702 | 0.200 | 0.387 | 0.496 |
| **sparse_negative=0.12 (default)** | **0.069** | **0.061** | **0.211** | **0.235** | **0.703** | **0.201** | **0.390** | **0.496** |
| sparse_negative=0.25 | 0.069 | 0.061 | 0.213 | 0.236 | 0.709 | 0.203 | 0.397 | 0.497 |
| sparse_negative=0.5 | 0.071 | 0.062 | 0.216 | 0.242 | **0.713** | 0.207 | 0.407 | 0.501 |
| sparse_negative=1 | 0.069 | 0.060 | 0.218 | 0.250 | 0.711 | 0.215 | 0.424 | 0.499 |
| sparse_negative=2 | 0.070 | 0.061 | 0.223 | 0.256 | 0.704 | 0.227 | 0.446 | 0.491 |
| warm_popularity=0.05 | 0.074 | 0.066 | 0.152 | 0.202 | 0.707 | 0.188 | 0.364 | 0.491 |
| warm_popularity=0.1 | 0.079 | 0.071 | 0.113 | 0.182 | 0.694 | 0.184 | 0.346 | 0.470 |
| warm_popularity=0.2 | 0.084 | 0.076 | 0.067 | 0.141 | 0.680 | 0.189 | 0.331 | 0.473 |
| hidden_gem=0 | 0.060 | 0.052 | 0.312 | 0.329 | 0.635 | 0.246 | 0.375 | 0.509 |
| similarity=0 | 0.071 | 0.064 | 0.168 | 0.207 | 0.712 | 0.201 | 0.396 | 0.465 |
| similarity=0.4 | 0.064 | 0.056 | 0.252 | 0.252 | 0.694 | 0.208 | 0.390 | 0.494 |
| sparse_positive=0.4 | 0.064 | 0.057 | 0.217 | 0.201 | 0.711 | 0.207 | 0.405 | 0.379 |
| sparse_positive=0.8 | 0.071 | 0.062 | 0.216 | 0.259 | 0.700 | 0.201 | 0.382 | 0.575 |

**Is `sparse_negative=0.12` too weak?** Somewhat, and it matters less than
expected. AUC rises steadily from 0 to 0.5 and falls after it; past 0.5 the
penalty starts pushing *liked* titles down too (`like_rank_pct` 0.201 → 0.227
at 2.0), because liked and disliked films share genres, decades and cast.
The whole range moves AUC by under 0.02 — far less than the gem bonus does.
Why: a MovieLens dislike is a film the user *chose* to watch, so its features
mostly cancel against the likes when the profile is accumulated, and few
features end up net-negative. A "Not for me" on a recommended card is a
cleaner negative; the online report will show whether it behaves differently.

### Proposals (not shipped)

**Proposal 1 — `sparse_negative` 0.12 → 0.5.** Recommended.

| setting | Δ recall | Δ ndcg | Δ novelty | Δ dislike_recall | Δ like_dislike_auc |
|---|---|---|---|---|---|
| k=20, 4.5k catalog | +0.0026 [−0.0010, +0.0071] | +0.0012 [−0.0001, +0.0026] | +0.0041 [+0.0025, +0.0057] | −0.0008 [−0.0022, 0.0000] | **+0.0103 [+0.0031, +0.0194]** |
| k=10, 4.5k catalog | +0.0014 [+0.0003, +0.0028] | +0.0013 [+0.0003, +0.0025] | +0.0050 [+0.0026, +0.0073] | +0.0002 [−0.0005, +0.0012] | +0.0103 [+0.0031, +0.0194] |
| k=20, full 9.6k catalog | −0.0005 [−0.0027, +0.0020] | −0.0005 [−0.0017, +0.0007] | +0.0040 [+0.0026, +0.0055] | −0.0001 [−0.0004, 0.0000] | +0.0084 [+0.0020, +0.0161] |

Better separation of likes from dislikes in every setting, slightly more
novel slates, and no accuracy cost anywhere (a small gain at k=10). Small,
consistent and cheap; it does not change director or gem behaviour. Ship it
behind the online report: compare the negative-action rate before and after.

**No second weight change is proposed.** Everything else measured is a trade,
not an improvement:

* `warm_popularity` 0.05 (+7.5% recall, −28% novelty, −14% coverage) and
  `similarity` 0 (+6% ndcg, −20% novelty) buy accuracy with discovery.
* `sparse_positive` 0.8, alone or with proposal 1, adds coverage but cancels
  proposal 1's AUC gain and raises director over-limit slates from 50% to 58%.
* `low_rating` 0.2 on the director-cap branch (below): +0.004 recall with an
  interval spanning zero, and a certain novelty cost (−0.018).

The levers that matter are not weights: points 5–7 above.

### The unmerged director-cap / percentile-gem branch

`florent/ecstatic-hypatia-gexedy` (commit `133e8b8`, no pull request) adds a
per-director cap, a franchise cap and catalog-percentile hidden gems. It is
not in `master`, so the live site does not have it. Its own `rank_titles`,
run through this harness on the same users with catalog percentiles computed
as its SQL does (franchise data is not in the evaluation cache, so that cap is
idle):

| production config | master | branch | paired Δ |
|---|---|---|---|
| director_over_limit | 0.496 | **0.000** | −0.496 [−0.535, −0.454] |
| gem_famous_share | 0.962 | **0.000** | −0.960 [−0.969, −0.949] |
| gem_fame_pct | 0.970 | 0.233 | |
| recall@20 | 0.069 | 0.054 | **−0.0150 [−0.0227, −0.0066]** |
| ndcg@20 | 0.061 | 0.047 | −0.0135 [−0.0184, −0.0081] |
| novelty | 0.211 | 0.378 | +0.167 [+0.161, +0.172] |
| coverage | 0.235 | 0.315 | |

It fixes both launch checks and costs 22% of offline recall — about 60% of
which is the quality prior that the broken gem rule was providing (point 6).
Offline recall rewards the famous, well-rated films this branch stops
boosting, so this is the number most likely to overstate the real cost;
measure it online before deciding it is a loss. (Scratch measurement, not
part of the committed harness: the branch predates the `candidate_channels`
split.)

### Caveats — read before trusting any number above

* **Offline accuracy rewards re-finding, not discovering.** A held-out title
  is one the user found *without* CineTaste. Novelty and hidden gems are
  penalised by construction.
* **MovieLens is a 1996–2018, English-language, mostly-US population** rating
  films they chose to see. Dislikes are therefore "near misses", not the
  "Not for me" a recommended card gets.
* **TMDb metadata is from today**, ratings are from years ago. `popularity`
  and `vote_count` describe 2026, so popularity-based strategies see the
  future's view of which films lasted. This flatters `popular` and the gem
  bonus.
* **Production sizing is approximate.** The catalog is the 4,500 most-voted
  matched titles, not production's ingest mix (top-rated, per-language and
  per-decade passes, TV). Exact cosine stands in for the HNSW index, so
  production's retrieval is if anything slightly worse than measured.
* **One dataset, one split, ~20 variants.** A few intervals that exclude zero
  are expected by chance; trust trends across settings (like proposal 1's
  dose-response and its k=10 / full-catalog replication), not a single cell.
* **Profiles are not time-decayed** in the harness (`half_life_days=None`);
  the API decays with a 365-day half-life (`TASTE_HALF_LIFE_DAYS`). MovieLens
  timestamps span years, so production would weight a user's recent ratings
  more than this evaluation does.

## Results: real ratings, MovieLens metadata only (September 2026)

Kept for history: these runs used only MovieLens genres, tags and year, and
the `popular` baseline here counted *all* ratings, including the held-out
period — a leak the November dataset removes (`train_interactions`). The
"hybrid (production)" row is `hybrid` above, not what production serves.

193 users from [MovieLens ml-latest-small](https://grouplens.org/datasets/movielens/),
temporal split, slate of 20.

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.001 | 0.001 | 0.026 | 0.006 | 0.724 | 0.351 | 0.511 | 0.329 |
| **popular** | **0.072** | **0.070** | **0.415** | **0.169** | 0.817 | 0.309 | 0.002 | 0.015 |
| dense_only | 0.015 | 0.011 | 0.109 | 0.032 | 0.132 | 0.071 | 0.402 | 0.109 |
| sparse_only | 0.036 | 0.039 | 0.347 | 0.120 | 0.720 | 0.256 | 0.142 | 0.049 |
| hybrid_no_diversity | 0.022 | 0.023 | 0.202 | 0.069 | 0.393 | 0.141 | 0.320 | 0.070 |
| hybrid | 0.025 | 0.028 | 0.254 | 0.085 | 0.614 | 0.232 | 0.225 | 0.062 |
| hybrid_with_exploration | 0.025 | 0.028 | 0.249 | 0.084 | 0.610 | 0.233 | 0.232 | 0.065 |
| hybrid_plus_popularity | 0.049 | 0.050 | 0.404 | 0.127 | 0.711 | 0.242 | 0.044 | 0.045 |

What it changed: the hashed content vector alone was the weakest signal and
the interpretable features the strongest, so weight moved from the vector to
the features (similarity 0.42 → 0.2, feature overlap 0.40 → 0.6). The
November run confirms that ordering with real metadata (`sparse_only` 0.075
vs `dense_only` 0.028 recall).

## Results: synthetic benchmark

800 titles, 200 users, seed 7, 3 held-out positives each, slate of 20.
Generated with `build_synthetic_dataset`.

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.027 | 0.011 | 0.080 | 0.009 | 0.859 | 0.377 | 0.499 | 0.995 |
| popular | 0.022 | 0.010 | 0.065 | 0.010 | 0.872 | 0.399 | 0.012 | 0.028 |
| dense_only | 0.163 | 0.080 | 0.435 | 0.081 | 0.444 | 0.052 | 0.533 | 0.665 |
| sparse_only | 0.125 | 0.062 | 0.350 | 0.066 | 0.569 | 0.087 | 0.538 | 0.598 |
| hybrid_no_diversity | 0.143 | 0.071 | 0.395 | 0.072 | 0.529 | 0.077 | 0.538 | 0.542 |
| hybrid | 0.095 | 0.047 | 0.265 | 0.048 | 0.750 | 0.186 | 0.551 | 0.425 |
| hybrid_with_exploration | 0.095 | 0.047 | 0.265 | 0.048 | 0.751 | 0.186 | 0.551 | 0.420 |
| hybrid_plus_popularity | 0.098 | 0.057 | 0.260 | 0.071 | 0.762 | 0.195 | 0.193 | 0.357 |
| **production** | **0.093** | **0.047** | **0.265** | **0.048** | **0.749** | **0.189** | **0.549** | **0.420** |

* **The taste model learns taste.** Ranking by taste finds 3.5–6× more held-out
  favourites than random or popular.
* **Diversity has a measured price here** (0.143 → 0.095 recall for 2.4× the
  genre variety), unlike on real data, where it is free. A fixed genre cap of
  4 cost ~60% of recall, which is why the cap is proportional to slate size
  (40%) and MMR λ is 0.8.
* **It is partly circular.** Synthetic users are generated with preferences
  over the same kinds of features the recommender scores, so accuracy is
  optimistic. Use it to compare strategies and catch regressions; use the
  real-data run for reality.

## Online: what users do with their picks

Offline metrics only reward re-finding what a user already rated. Once the
soft launch has run for a couple of weeks, `recommendation_impressions` and
`interaction_events` answer the question that matters: did people act on
what they were shown?

```bash
cd backend
python -m app.scripts.engagement_report                         # last 28 days
python -m app.scripts.engagement_report --since 2026-12-01 --window-days 7
python -m app.scripts.engagement_report --print-sql             # the queries, for psql
```

Per (user, title) at its **first** impression in the period, the user's first
explicit action within the window — rating, watchlist, like/dislike, not
interested, watched — split into positive and negative by the signal policy
table. Reported overall, by slate position, by leading reason, by any reason
on the card and by action type, with 95% Wilson intervals (cells are small in
an invite-only launch).

It is **read-only and local-only**: the connection runs with
`default_transaction_read_only=on` and any non-local database host is refused,
with no override. To analyse production data, restore a backup locally first
([RUNBOOK.md](RUNBOOK.md#5-database-backup-and-restore)).

Caveats: an action on a title the user later found through search still
counts (attribution is not causation), and only slates computed fresh are
logged (cache hits are not), so one impression can stand for several views.

What to look at first: action rate by position (does the first row earn its
place?), negative rate on `hidden_gem` and `discovery` reasons (do stretches
annoy?), and the negative rate before and after any weight change such as
proposal 1.

## Reproducing

```bash
# 1. MovieLens, outside the repository (GroupLens forbids redistribution)
mkdir -p ~/.cache/cinetaste/eval && cd ~/.cache/cinetaste/eval
curl -LO https://files.grouplens.org/datasets/movielens/ml-latest-small.zip
unzip ml-latest-small.zip && cd -

# 2. TMDb details for every MovieLens film: ~9.7k requests, ~8 min at 20/s.
#    Resumable; cached in ~/.cache/cinetaste/eval/tmdb_movies.jsonl (~23 MB).
cd backend
python -m app.scripts.fetch_eval_metadata --movielens ~/.cache/cinetaste/eval/ml-latest-small

# 3. The main tables (~3 min)
ML=~/.cache/cinetaste/eval/ml-latest-small
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0

# 4. Weight sweeps on the production configuration (~4 min)
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0 \
    --only-variants --sweep sparse_negative=0,0.06,0.25,0.5,1.0,2.0 \
    --sweep warm_popularity=0.05,0.1,0.2 --sweep hidden_gem=0 \
    --sweep similarity=0,0.4 --sweep sparse_positive=0.4,0.8

# 5. Proposal 1 at another slate size and on the full catalog
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 10 --users 0 \
    --only-variants --variant proposal_1:sparse_negative=0.5
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0 \
    --catalog-size 0 --only-variants --variant proposal_1:sparse_negative=0.5

# Synthetic benchmark (seconds, no downloads)
python -m app.scripts.evaluate_recommender -k 20 --users 200
```

`fetch_eval_metadata` needs `TMDB_API_KEY` like ingest does (where a proxy
injects the token, any placeholder works). It requests at 20/s — well under
TMDb's ~50/s — and stops, keeping what it has, if TMDb rate-limits; re-run to
resume. The cache and MovieLens are personal working copies: they are refused
inside the repository, never committed and can be deleted at any time.

The real-data run is deterministic (all users, fixed split); only the
`random` baseline is seeded. The 2026-11-21 TMDb snapshot will drift as TMDb
updates popularity and votes, so expect small changes on a fresh fetch.

## Adding a strategy

`DEFAULT_STRATEGIES` in `app/recommendation/evaluation.py` maps a name to a
callable `(user, dataset, k) → ranked ids or RankedItems`.
`make_ranking_strategy(weights=..., mmr_lambda=..., max_per_genre=...,
candidate_pool="production")` wraps the production ranker, and
`production_variant(**weights)` is production with some weights changed — so
weight changes can be measured, with paired intervals, before shipping:

```python
"more_dense": production_variant(similarity=0.6, sparse_positive=0.3),
```

or without code: `--variant more_dense:similarity=0.6,sparse_positive=0.3`.
A user's profile and per-candidate channels (`candidate_channels`) are computed
once and shared by every strategy, so a variant costs one blend and one slate
selection per user.
