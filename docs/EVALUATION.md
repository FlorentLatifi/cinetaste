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
| `hybrid_no_diversity` | Production scoring, diversity controls off (no MMR, no genre, director or franchise cap). |
| `hybrid` | Production scoring + MMR λ=0.8 + genre, director and franchise caps, over the whole catalog, no exploration. |
| `hybrid_with_exploration` | `hybrid` plus 3 exploration slots. |
| `hybrid_plus_popularity` | `hybrid` with `warm_popularity=0.2`. |
| **`production`** | **What the API serves**: the production candidate pool, MMR λ=0.8, genre / director / franchise caps, 3 exploration slots (`Settings.rec_*`). |

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
* `franchise_repeat` — share of slates where one TMDb collection (a franchise)
  holds 2+ titles. The franchise cap allows one per 20 cards.
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
production ranks on. Catalog percentiles are stored the way the ingest SQL
stores them, and TMDb collections (franchises) the way ingest stores them.

| Dataset | |
|---|---|
| MovieLens ml-latest-small movies with a TMDb id | 9,733 |
| … with TMDb details (113 ids no longer exist on TMDb) | 9,620 |
| … of which belong to a TMDb collection (franchise) | 2,110 |
| Ratings kept (of 100,836) | 100,515 |
| Catalog (candidates): the most-voted titles, production-sized | 4,500 |
| Users evaluated (≥ 1 held-out like in the catalog) | 581 |
| Held-out likes (≥ 4★) / dislikes (≤ 2★, from 311 users) | 8,019 / 1,930 |

Temporal split per user (latest 20% held out), slate of 20. TMDb metadata
fetched 2026-11-21. The tables describe the current code: per-director and
per-franchise caps, hidden gems on catalog percentiles, and `sparse_negative`
0.5. What `master` scored before the caps and gems, paired user by user, is in
[Before and after](#before-and-after-director-and-franchise-caps-catalog-percentile-gems).

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.003 | 0.003 | 0.053 | 0.010 | 0.945 | 0.443 | 0.508 | 0.918 |
| popular (TMDb) | 0.035 | 0.029 | 0.275 | 0.059 | 0.911 | 0.341 | 0.003 | 0.013 |
| popular_ml | **0.093** | **0.082** | 0.444 | **0.178** | 0.927 | 0.425 | 0.064 | 0.034 |
| dense_only | 0.014 | 0.011 | 0.107 | 0.024 | 0.821 | 0.290 | 0.629 | 0.208 |
| sparse_only | 0.060 | 0.052 | 0.384 | 0.117 | 0.893 | 0.348 | 0.347 | 0.324 |
| hybrid_no_diversity | 0.056 | 0.046 | 0.356 | 0.100 | 0.884 | 0.341 | 0.418 | 0.320 |
| hybrid | 0.042 | 0.039 | 0.336 | 0.103 | 0.911 | 0.380 | 0.457 | 0.324 |
| hybrid_with_exploration | 0.041 | 0.038 | 0.325 | 0.102 | 0.912 | 0.485 | 0.464 | 0.314 |
| hybrid_plus_popularity | 0.071 | 0.065 | **0.468** | 0.147 | 0.915 | 0.362 | 0.098 | 0.237 |
| **production** | **0.053** | **0.046** | **0.382** | **0.112** | **0.887** | **0.430** | **0.383** | **0.326** |

| strategy | dislike_recall@20 | like_rank_pct | dislike_rank_pct | like_dislike_auc | director_max | director_over_limit | franchise_repeat | gem_labels | gem_fame_pct | gem_famous_share |
|---|---|---|---|---|---|---|---|---|---|---|
| random | 0.003 | – | – | – | 1.19 | 0.005 | 0.019 | – | – | – |
| popular (TMDb) | 0.022 | 0.272 | 0.342 | 0.568 | 2.95 | 0.957 | 0.993 | – | – | – |
| popular_ml | 0.028 | 0.146 | 0.204 | 0.593 | 2.94 | 0.788 | 0.883 | – | – | – |
| sparse_only | 0.008 | 0.245 | 0.387 | 0.653 | 5.04 | 0.890 | 0.668 | 1.5 | 0.256 | 0.000 |
| hybrid_no_diversity | 0.008 | 0.262 | 0.408 | 0.657 | 4.48 | 0.826 | 0.599 | 2.4 | 0.244 | 0.000 |
| hybrid | 0.012 | 0.262 | 0.408 | 0.657 | 1.92 | 0.000 | 0.000 | 2.8 | 0.234 | 0.000 |
| **production** | **0.007** | **0.262** | **0.408** | **0.657** | **1.93** | **0.000** | **0.000** | **2.4** | **0.232** | **0.000** |

### What it says

**1. With real metadata the recommender beats the popularity production can
see — but not in-domain popularity.** Production's recall is 1.5× the TMDb
popularity baseline (0.053 vs 0.035; paired Δ −0.018 [−0.029, −0.007] for
`popular`). The most-rated-in-MovieLens baseline still wins on recall (Δ
+0.040 [+0.025, +0.055]) and MRR, and gets there by recommending the charts:
novelty 0.064 vs 0.383, coverage 3% vs 33% of the catalog. Part of that win is
the protocol, not taste: MovieLens users rate what they watch, and what this
population watched next is what was popular with this population — an
advantage no content model can have.

**2. Popularity still buys accuracy at a steep price.** `warm_popularity`
0.05 adds +0.0095 recall and costs 0.10 novelty; 0.2 adds +0.025 and costs
0.28 (sweep below). The September decision — off by default in a discovery
product — still holds. A *quality* prior buys the same accuracy for less
novelty (point 6).

**3. Diversity costs little; candidate retrieval now helps.** The director
and franchise caps together cost Δ recall −0.0036 [−0.0072, −0.0003]
(−0.0019 [−0.0058, +0.0021] with no gem bonus on either side)
(`caps_off` in the sweep), the franchise cap alone −0.0020 [−0.0039,
−0.0006]. Exploration slots raise genre variety from 0.38 to 0.43–0.49.
Ranking production's ANN+popular pool now beats ranking the whole catalog
(Δ +0.012 [+0.005, +0.020] vs `hybrid_with_exploration`): over all 4,500
titles the gem bonus reaches many more obscure films than the pool holds.

**4. Dislikes are respected, mostly by the content itself.** A held-out
disliked title reaches the slate at a seventh of the rate of a liked one
(0.007 vs 0.053), and the score separates likes from dislikes with AUC 0.657
(TMDb popularity: 0.568). That AUC was 0.703 before the caps and gems: the old
gem bonus was also the strongest like/dislike separator (point 6).
`sparse_negative` 0.5 (was 0.12) contributes +0.012 of today's 0.657.

**5. "Hidden gem" now means a hidden gem.** None of production's gem labels
land on the catalog's most-voted 10% (was 96%), and the mean labelled title
sits at the 23rd fame percentile (was the 97th). The old rule required TMDb
`popularity < 40`, but TMDb's popularity is a trending score whose scale has
shrunk — median 2.5, 99th percentile 20, 99.8% of titles below 40 — so it
meant "★7.2+ with 50 votes" and labelled Interstellar, Inception and The
Shawshank Redemption. The rule now reads catalog percentiles stored by ingest:
well rated, at least 50 votes, and below the 40th percentile of the catalog on
*both* popularity and vote count. Labels are rationed to the strong tier, 2.4
per slate on average (always 3 before). Most frequent labels across the 581
users' slates:

| before (`master`) | votes rank of 4,500 | after | votes rank of 4,500 |
|---|---|---|---|
| Inglourious Basterds ★8.2 | #24 | A Simple Plan ★7.2 | #3,161 |
| Pulp Fiction ★8.5 | #10 | The Lady from Shanghai ★7.3 | #3,996 |
| Se7en ★8.4 | #30 | Imagine Me & You ★7.6 | #3,187 |
| The Green Mile ★8.5 | #75 | Manhattan Murder Mystery ★7.2 | #3,079 |
| Forrest Gump ★8.5 | #11 | Paper Moon ★7.9 | #3,666 |

`gem_famous_share` is 0 by construction under the new rule (a label needs
both percentiles below 0.4; the metric flags vote-count percentiles ≥ 0.9),
so `gem_fame_pct` and the examples are the informative numbers.

**6. The old gem bonus was the quality prior — and fixing the label removed
it.** It fired on almost every well-rated film, so it ranked by rating as a
side effect: `hidden_gem=0` on `master` costs 13% of recall (Δ −0.0089
[−0.0159, −0.0011]) and drops AUC from 0.70 to 0.64. The new bonus fires on
obscure films only, which offline recall penalises by construction: on the
new rules `hidden_gem=0` *gains* +0.0063 [+0.0026, +0.0103] recall and loses
0.064 novelty. An explicit quality prior (a vote-count-shrunk rating
percentile, `quality`, off by default) restores what the accident provided —
see the quality-prior proposal under [Weight changes](#weight-changes).

**7. The director check (S2) passes, and franchises no longer repeat.** No
production slate gives one director more than 2 of 20 cards (was 49.6%; mean
maximum 1.9, was 2.7), and none holds two films of one franchise (was 29%).
Without MMR and the caps (`hybrid_no_diversity`) it would be 83% and 60%.

### Before and after: director and franchise caps, catalog-percentile gems

`master` scored with its own code and the same data, then paired user by user
(`--against`, see [Reproducing](#reproducing)). 581 users, slate of 20:

| production | `master` | this change | paired Δ |
|---|---|---|---|
| director_over_limit (S2) | 0.496 | **0.000** | −0.496 [−0.535, −0.454] |
| franchise_repeat | 0.294¹ | **0.000** | |
| gem_famous_share | 0.963 | **0.000** | −0.961 [−0.970, −0.951] |
| gem_fame_pct | 0.970 | 0.233 | |
| recall@20 | 0.069 | 0.052 | **−0.0170 [−0.0247, −0.0087]** |
| ndcg@20 | 0.061 | 0.046 | −0.0148 [−0.0200, −0.0093] |
| hit_rate@20 | 0.448 | 0.375 | −0.072 [−0.108, −0.036] |
| like_dislike_auc | 0.703 | 0.645 | −0.058 [−0.077, −0.041] |
| dislike_recall@20 | 0.012 | 0.007 | −0.0055 [−0.0139, −0.0004] |
| novelty | 0.211 | 0.382 | +0.171 [+0.165, +0.176] |
| coverage | 0.235 | 0.316 | |

¹ `master`'s harness has no franchise metric; measured once by joining its
slates to the same TMDb collections (not part of the committed harness).

Both columns use `sparse_negative` 0.12, the default at the time; moving it
to 0.5 afterwards put production at the numbers in the main table (recall
0.053, AUC 0.657 — see [Weight changes](#weight-changes)).

Both launch checks are fixed, and offline recall drops 25%. Where the loss
comes from, step by step (recall@20, paired):

| step | recall@20 | Δ |
|---|---|---|
| `master` | 0.069 | |
| remove the old gem bonus (`master`, `hidden_gem=0`) | 0.060 | −0.0089 [−0.0159, −0.0011] |
| add the director and franchise caps, still no gem bonus | 0.058 | −0.0019 [−0.0058, +0.0021] |
| add the catalog-percentile gem bonus (this change) | 0.052 | −0.0063 [−0.0103, −0.0026] |

So the caps cost little (a tenth of the loss); the gem bonus is the rest:
about half of the loss is the accidental quality prior going away, about a
third is the new bonus promoting genuinely little-known films. That third is
the product working as intended and offline recall counting it as a loss —
a held-out film is one the user found *without*
CineTaste, and well-known films are the ones people find without help. This
is the number most likely to overstate the real cost; the online report
([below](#online-what-users-do-with-their-picks)) settles it once soft-launch
traffic exists. The quality-prior proposal shows the accuracy can be bought
back without undoing either fix.

An earlier scratch run of `florent/ecstatic-hypatia-gexedy`'s own ranker
measured −0.0150 [−0.0227, −0.0066]. The difference is mostly the franchise
cap (−0.0020 on its own, see `franchise cap off` below), which that run could
not exercise: the evaluation cache had no collections then.

### Weight sweeps (production configuration)

Each row changes one weight (or cap) of `production`; deltas are paired
against it. The sweep was run with the previous default `sparse_negative`
0.12 as the base, before the change it motivated. Every row except the two cap rows keeps `director_over_limit`,
`franchise_repeat` and `gem_famous_share` at 0; with both caps off,
`director_over_limit` is 0.489 and `franchise_repeat` 0.269, and with only the
franchise cap off `franchise_repeat` is 0.229.

| variant | recall@20 | ndcg@20 | novelty | coverage | like_dislike_auc | like_rank_pct | dislike_rank_pct |
|---|---|---|---|---|---|---|---|
| sparse_negative=0 | 0.052 | 0.046 | 0.381 | 0.310 | 0.639 | 0.253 | 0.383 |
| sparse_negative=0.06 | 0.052 | 0.046 | 0.382 | 0.314 | 0.645 | 0.254 | 0.387 |
| sparse_negative=0.12 (previous default) | 0.052 | 0.046 | 0.382 | 0.316 | 0.645 | 0.255 | 0.390 |
| sparse_negative=0.25 | 0.052 | 0.046 | 0.383 | 0.320 | 0.651 | 0.258 | 0.398 |
| **sparse_negative=0.5 (default)** | **0.053** | **0.046** | **0.383** | **0.326** | **0.657** | **0.262** | **0.408** |
| sparse_negative=1 | 0.053 | 0.045 | 0.386 | 0.335 | 0.657 | 0.270 | 0.424 |
| sparse_negative=2 | 0.054 | 0.046 | 0.387 | 0.343 | 0.655 | 0.280 | 0.445 |
| warm_popularity=0.05 | 0.061 | 0.054 | 0.278 | 0.300 | 0.653 | 0.223 | 0.356 |
| warm_popularity=0.1 | 0.071 | 0.061 | 0.197 | 0.269 | 0.651 | 0.206 | 0.333 |
| warm_popularity=0.2 | 0.077 | 0.068 | 0.100 | 0.211 | 0.648 | 0.199 | 0.315 |
| quality=0.05 | 0.060 | 0.053 | 0.329 | 0.290 | 0.696 | 0.218 | 0.397 |
| quality=0.1 | 0.067 | 0.059 | 0.280 | 0.253 | 0.726 | 0.198 | 0.407 |
| quality=0.2 | 0.078 | 0.069 | 0.206 | 0.202 | **0.758** | 0.188 | 0.431 |
| low_rating=0.2 | 0.055 | 0.048 | 0.364 | 0.281 | 0.727 | 0.223 | 0.443 |
| hidden_gem=0 | 0.058 | 0.051 | 0.318 | 0.329 | 0.635 | 0.246 | 0.375 |
| similarity=0 | 0.059 | 0.052 | 0.329 | 0.303 | 0.656 | 0.251 | 0.392 |
| similarity=0.4 | 0.048 | 0.041 | 0.416 | 0.320 | 0.631 | 0.266 | 0.392 |
| sparse_positive=0.4 | 0.045 | 0.040 | 0.446 | 0.306 | 0.640 | 0.268 | 0.402 |
| sparse_positive=0.8 | 0.056 | 0.049 | 0.351 | 0.318 | 0.647 | 0.249 | 0.384 |
| caps off (director, franchise) | 0.055 | 0.048 | 0.376 | 0.321 | 0.645 | 0.255 | 0.390 |
| franchise cap off | 0.054 | 0.047 | 0.378 | 0.315 | 0.645 | 0.255 | 0.390 |

**Was `sparse_negative=0.12` too weak?** Somewhat — it is now 0.5 — and it
matters less than expected. AUC rises from 0.639 (0) to 0.657 (0.5–1) and falls after it; past
0.5 the penalty starts pushing *liked* titles down too (`like_rank_pct` 0.255
→ 0.280 at 2.0), because liked and disliked films share genres, decades and
cast. The whole range moves AUC by under 0.02 — far less than a quality prior
does. Why: a MovieLens dislike is a film the user *chose* to watch, so its
features mostly cancel against the likes when the profile is accumulated, and
few features end up net-negative. A "Not for me" on a recommended card is a
cleaner negative; the online report will show whether it behaves differently.

**Quality beats popularity on the accuracy–novelty frontier.** At equal
novelty, `quality=0.1` (0.280) has more recall than `warm_popularity=0.05`
(0.278): 0.067 vs 0.061, with AUC 0.726 vs 0.653; `quality=0.2` vs
`warm_popularity=0.1` at ~0.20 novelty: 0.078 vs 0.071. Ranking films people
rate well is a better prior than ranking films people know.

### Weight changes

**Shipped 2026-11-22 — low-rating penalty `low_rating` 0 → 0.3.**

Swept on the same 581 users, paired against production with the quality prior
and language / TV caps already on:

| `low_rating` | Δ recall@20 | Δ ndcg@20 | Δ novelty | Δ dislike_recall@20 | Δ like_dislike_auc | coverage |
|---|---|---|---|---|---|---|
| 0 (before) | — | — | — | — | — (0.705) | 0.309 |
| 0.1 | +0.0007 [−0.0036, +0.0037] | +0.0006 | −0.0061 | +0.0009 [0.0000, +0.0025] | +0.0355 [+0.0235, +0.0470] | 0.286 |
| 0.2 | +0.0005 [−0.0039, +0.0036] | +0.0006 | −0.0074 | +0.0009 [0.0000, +0.0025] | +0.0484 [+0.0323, +0.0656] | 0.279 |
| **0.3** | **+0.0005 [−0.0040, +0.0036]** | **+0.0006** | **−0.0080** | **+0.0003 [0.0000, +0.0008]** | **+0.0479 [+0.0284, +0.0672]** | **0.274** |
| 0.5 | +0.0006 [−0.0038, +0.0038] | +0.0007 | −0.0087 | +0.0003 [0.0000, +0.0008] | +0.0522 [+0.0314, +0.0734] | 0.271 |

Accuracy does not move at any weight; the like/dislike separation rises to
about +0.048 by 0.2 and then levels off. 0.3 was taken over 0.2 because held-out
dislikes reach the slate no more often than with the penalty off (+0.0003, vs
+0.0009), and because, estimated from its live score (0.559 − 0.3 × 0.205),
it moves a well-known ★6.3 sequel (*The Matrix Resurrections*) from second
place to about fifth for a fan of the original; MMR decides the exact spot. The price is coverage (0.309 → 0.274): the
titles that stop being recommended are mostly the poorly rated ones. Production
after this change: recall@20 0.063, ndcg@20 0.054, hit rate 0.418, novelty
0.326, AUC 0.753.

**Shipped 2026-11-22 — quality prior `quality` 0 → 0.05, and language / TV caps.**

Measured on the same 581 users, 4,500-title catalog and k=20, paired user by
user against the code before the change (`--against`); the baseline reproduced
the main table above (production recall 0.052).

| production | before | after | paired Δ |
|---|---|---|---|
| recall@20 | 0.052 | **0.062** | +0.0109 [+0.0058, +0.0169] |
| ndcg@20 | 0.045 | 0.053 | +0.0081 [+0.0053, +0.0109] |
| hit_rate@20 | 0.377 | 0.413 | |
| novelty | 0.390 | 0.334 | −0.0559 [−0.0595, −0.0524] |
| coverage | 0.328 | 0.309 | |
| dislike_recall@20 | 0.008 | 0.007 | −0.0009 [−0.0026, +0.0003] |
| like_dislike_auc | 0.657 | **0.705** | +0.0476 [+0.0387, +0.0572] |
| director_over_limit / franchise_repeat / gem_famous_share | 0 / 0 / 0 | 0 / 0 / 0 | unchanged |

Isolated (variants of the new production, same users):

| variant | Δ recall@20 | Δ novelty | Δ like_dislike_auc |
|---|---|---|---|
| `quality=0` (prior off) | −0.0094 [−0.0134, −0.0055] | +0.0561 | −0.0476 |
| `max_per_language=999,max_tv=999` (mix caps off) | −0.0021 [−0.0063, +0.0004] | +0.0009 | 0.0000 |

* **The quality prior** is the halfway point proposed below (0.05 rather than
  0.1): a fifth more held-out favourites found and a clearly better
  like/dislike separation, for a sixth of the novelty. The online check is
  `engagement_report`: positive-action rate should rise, negative-action rate
  should not. It also stops a well-known but poorly rated sequel (live: *The
  Matrix Resurrections*, ★6.3) leading a slate on director overlap alone.
* **The language and TV caps cost nothing measurable here** — MovieLens users
  rate mostly English films, and the caps leave a user's own language alone
  (`language_max_share` 0.951 vs 0.975 with them off). They exist for the
  case the benchmark barely has: a mixed-language rater. Live, four
  non-English favourites produced a guest slate with 11 of 20 Japanese titles,
  two of them anime TV series; `tests/test_slate_mix_caps.py` pins that case.
  MovieLens has no TV, so `tv_share` is 0 on every row.

New columns: `language_max_share` (largest share of a slate one original
language holds) and `tv_share`.

**Shipped 2026-11-21 — `sparse_negative` 0.12 → 0.5.**

| setting | Δ recall | Δ ndcg | Δ novelty | Δ dislike_recall | Δ like_dislike_auc |
|---|---|---|---|---|---|
| k=20, 4.5k catalog, current rules | +0.0014 [−0.0000, +0.0030] | +0.0006 [−0.0003, +0.0016] | +0.0012 [−0.0006, +0.0029] | +0.0008 [0.0000, +0.0022] | **+0.0119 [+0.0023, +0.0235]** |
| k=10, 4.5k catalog, current rules | −0.0002 [−0.0016, +0.0011] | −0.0004 [−0.0016, +0.0007] | +0.0033 [+0.0007, +0.0059] | 0.0000 [0.0000, 0.0000] | +0.0119 [+0.0023, +0.0235] |
| k=20, 4.5k catalog, rules before the caps | +0.0026 [−0.0010, +0.0071] | +0.0012 [−0.0001, +0.0026] | +0.0041 [+0.0025, +0.0057] | −0.0008 [−0.0022, 0.0000] | +0.0103 [+0.0031, +0.0194] |
| k=10, 4.5k catalog, rules before the caps | +0.0014 [+0.0003, +0.0028] | +0.0013 [+0.0003, +0.0025] | +0.0050 [+0.0026, +0.0073] | +0.0002 [−0.0005, +0.0012] | +0.0103 [+0.0031, +0.0194] |
| k=20, full 9.6k catalog, rules before the caps | −0.0005 [−0.0027, +0.0020] | −0.0005 [−0.0017, +0.0007] | +0.0040 [+0.0026, +0.0055] | −0.0001 [−0.0004, 0.0000] | +0.0084 [+0.0020, +0.0161] |

Better separation of likes from dislikes in every setting (AUC +0.010 to
+0.012) and no measurable accuracy cost anywhere; it does not change director
or gem behaviour. The first two rows were re-measured independently, on a
fresh TMDb download, and reproduce PR #61's figures exactly. One honest wrinkle:
at k=20 on the current rules, held-out dislikes reach the slate very slightly
*more* often (+0.0008, interval touching zero) even though the score ranks
them lower overall — the penalty reorders the head of the list, and a few
near-miss dislikes move up as stronger-penalised ones move out. Watch the
negative-action rate in `engagement_report` after deploy; it should not rise.

**Proposed 2026-11-21, shipped at 0.05 on 2026-11-22 (above) — a quality prior, `quality` 0 → 0.1.** For the
owner to decide: it is the trade the caps and gems exposed. Measured with
`sparse_negative` 0.12. The knob ranks by a
vote-count-shrunk rating (`quality_percentiles`, the IMDb top-list formula, as
a candidate-pool percentile), from `florent/fervent-keller-0l0j9b`.

| setting | Δ recall | Δ ndcg | Δ novelty | Δ like_dislike_auc |
|---|---|---|---|---|
| k=20 vs `master` | −0.0012 [−0.0078, +0.0059] | −0.0020 [−0.0062, +0.0021] | **+0.069 [+0.064, +0.073]** | **+0.023 [+0.008, +0.038]** |
| k=10 vs `master` | −0.0003 [−0.0059, +0.0051] | +0.0023 [−0.0029, +0.0070] | +0.080 [+0.073, +0.087] | +0.023 [+0.008, +0.038] |
| k=20 vs this change | +0.0158 [+0.0088, +0.0228] | +0.0128 [+0.0083, +0.0167] | −0.102 [−0.107, −0.098] | +0.081 [+0.064, +0.098] |
| k=10 vs this change | +0.0070 [+0.0004, +0.0133] | +0.0089 [+0.0041, +0.0134] | −0.098 [−0.104, −0.091] | +0.081 [+0.064, +0.098] |
| k=20, full 9.6k catalog (593 users), vs this change | +0.0125 [+0.0085, +0.0169] | +0.0106 [+0.0076, +0.0137] | −0.095 [−0.099, −0.091] | +0.090 [+0.073, +0.107] |

With it, production is as accurate as `master` was (intervals centred on
zero at both slate sizes), separates likes from dislikes *better* than
`master` did, keeps a third more novelty than `master` (0.280 vs 0.211;
coverage 0.253 vs 0.235), and keeps both launch checks at zero (it changes
the score, not the label rule). What it gives up is 60% of the novelty
this change added (0.382 → 0.280; `master` 0.211). `quality=0.05` is the halfway point:
recall −0.0087 [−0.0159, −0.0010] vs `master`, AUC level with it, novelty
0.329. The caveat that matters most: TMDb's ratings are from 2026, ratings in
MovieLens are from years before, so offline the prior knows how a film was
eventually judged — this flatters it. Ship behind the online report, like
the `sparse_negative` change, and compare positive-action rates before and after.

**No other weight change is proposed.** Everything else measured is a trade,
not an improvement:

* `warm_popularity` 0.05 (+0.0095 recall, −0.105 novelty) is dominated by
  `quality` (point above).
* `similarity` 0 (+0.0073 recall, −0.054 novelty) and `sparse_positive` 0.8
  (+0.0045, −0.031) buy accuracy with discovery.
* `low_rating` 0.2: +0.0034 [−0.0017, +0.0074] recall, an interval spanning
  zero, for a certain novelty cost (−0.018 [−0.021, −0.015]). It does lift
  AUC (+0.082) — it is a quality prior seen from below — but `quality` does
  that and recovers recall.

### Caveats — read before trusting any number above

* **Offline accuracy rewards re-finding, not discovering.** A held-out title
  is one the user found *without* CineTaste. Novelty and hidden gems are
  penalised by construction — which is most of what this change's recall
  loss measures.
* **MovieLens is a 1996–2018, English-language, mostly-US population** rating
  films they chose to see. Dislikes are therefore "near misses", not the
  "Not for me" a recommended card gets.
* **TMDb metadata is from today**, ratings are from years ago. `popularity`,
  `vote_count` and `vote_average` describe 2026, so popularity- and
  rating-based priors see the future's view of which films lasted. This
  flatters `popular`, the old gem bonus and the quality prior.
* **Production sizing is approximate.** The catalog is the 4,500 most-voted
  matched titles, not production's ingest mix (top-rated, per-language and
  per-decade passes, TV). Exact cosine stands in for the HNSW index, so
  production's retrieval is if anything slightly worse than measured.
* **Live percentiles need a backfill.** Production titles stored before this
  change have no `catalog_percentiles` until the next ingest or
  `reembed_catalog --percentiles-only` ([DEPLOY.md](DEPLOY.md)); until then
  ranking falls back to percentiles within the candidate pool, which this
  evaluation does not measure.
* **The caps scale with the slate.** 10% per director and 5% per franchise,
  minimum one each: at 20 cards (what For You and guest slates request) that
  is 2 and 1; below 15 cards it is one film per director.
* **One dataset, one split, ~25 variants.** A few intervals that exclude zero
  are expected by chance; trust trends across settings (like the proposals'
  dose-response and their replication at k=10), not a single cell.
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
| dense_only | 0.152 | 0.076 | 0.405 | 0.081 | 0.427 | 0.052 | 0.558 | 0.881 |
| sparse_only | 0.113 | 0.055 | 0.300 | 0.055 | 0.523 | 0.085 | 0.555 | 0.757 |
| hybrid_no_diversity | 0.135 | 0.066 | 0.360 | 0.066 | 0.490 | 0.077 | 0.569 | 0.806 |
| hybrid | 0.090 | 0.047 | 0.245 | 0.051 | 0.759 | 0.193 | 0.680 | 0.657 |
| hybrid_with_exploration | 0.090 | 0.047 | 0.245 | 0.051 | 0.759 | 0.193 | 0.680 | 0.655 |
| hybrid_plus_popularity | 0.110 | 0.067 | 0.285 | 0.084 | 0.755 | 0.190 | 0.120 | 0.393 |
| **production** | **0.090** | **0.047** | **0.245** | **0.051** | **0.756** | **0.197** | **0.672** | **0.665** |

* **The taste model learns taste.** Ranking by taste finds 3–7× more held-out
  favourites than random or popular.
* **Diversity has a measured price here** (0.135 → 0.090 recall for 2.5× the
  genre variety), unlike on real data, where it costs little. A fixed genre cap of
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
annoy?), and the negative rate before and after any weight change, such as
`sparse_negative` 0.12 → 0.5 (shipped 2026-11-21).

**Whether the director/franchise caps and percentile gems cost anything real.**
Offline they cost 25% of recall@20, mostly by recommending films users had not
already found on their own (see [Before and after](#before-and-after-director-and-franchise-caps-catalog-percentile-gems)).
Online that shows up, if it is real, as a lower *positive* action rate on
cards with the `hidden_gem` reason and overall, against a period before the
change. Its labels now go to films like *Paper Moon* rather than *Pulp
Fiction*: a save or a "seen it, loved it" on those is the discovery the
product is for, and a "Not for me" is the cost. Compare the two periods
with `--since`/`--window-days`, and run it again after the quality prior if it ships.

## Reproducing

```bash
# 1. MovieLens, outside the repository (GroupLens forbids redistribution)
mkdir -p ~/.cache/cinetaste/eval && cd ~/.cache/cinetaste/eval
curl -LO https://files.grouplens.org/datasets/movielens/ml-latest-small.zip
unzip ml-latest-small.zip && cd -

# 2. TMDb details for every MovieLens film: ~9.7k requests, ~8 min at 20/s.
#    Resumable; cached in ~/.cache/cinetaste/eval/tmdb_movies.jsonl (~23 MB).
#    Caches fetched before the franchise cap lack TMDb collections, so the cap
#    is idle on them: delete the file and fetch again to measure it.
cd backend
python -m app.scripts.fetch_eval_metadata --movielens ~/.cache/cinetaste/eval/ml-latest-small

# 3. The main tables (~3 min)
ML=~/.cache/cinetaste/eval/ml-latest-small
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0

# 4. Weight and cap sweeps on the production configuration (~5 min each).
#    The published sweep used the previous default sparse_negative=0.12 as its
#    base; on today's code every other row now sits on 0.5, so expect small shifts.
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0 \
    --only-variants --sweep sparse_negative=0,0.06,0.25,0.5,1.0,2.0 \
    --sweep warm_popularity=0.05,0.1,0.2 \
    --sweep similarity=0,0.4 --sweep sparse_positive=0.4,0.8
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0 \
    --only-variants --sweep hidden_gem=0 --sweep quality=0.05,0.1,0.2 --sweep low_rating=0.2 \
    --variant caps_off:max_per_director=999,max_per_collection=999 \
    --variant franchise_cap_off:max_per_collection=999 \
    --sweep sparse_negative=0.12                      # the previous default, paired against today's

# 5. The quality-prior proposal at another slate size and on the full catalog
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 10 --users 0 \
    --only-variants --sweep quality=0.05,0.1
python -m app.scripts.evaluate_recommender --dataset movielens-tmdb --path $ML -k 20 --users 0 \
    --catalog-size 0 --only-variants --sweep quality=0.05,0.1

# 6. A ranking change against master, paired user by user: the same command
#    on each side, --json on master, --against on the branch. Strategies are
#    matched by name; the dataset, users and k must be the same.
git switch master   && python -m app.scripts.evaluate_recommender --dataset movielens-tmdb \
    --path $ML -k 20 --users 0 --json /tmp/master.json
git switch <branch> && python -m app.scripts.evaluate_recommender --dataset movielens-tmdb \
    --path $ML -k 20 --users 0 --against /tmp/master.json

# Synthetic benchmark (seconds, no downloads)
python -m app.scripts.evaluate_recommender -k 20 --users 200
```

`fetch_eval_metadata` needs `TMDB_API_KEY` like ingest does (where a proxy
injects the token, any placeholder works). It requests at 20/s — well under
TMDb's ~50/s — and stops, keeping what it has, if TMDb rate-limits; re-run to
resume. The cache and MovieLens are personal working copies: they are refused
inside the repository, never committed and can be deleted at any time.

`--json` writes per-user rows, which `--against` needs; the harness wrote
summaries only before the franchise cap, so the first before/after above
dumped `master`'s rows with a scratch script calling `run_evaluation`.

The real-data run is deterministic (all users, fixed split); only the
`random` baseline is seeded. The 2026-11-21 TMDb snapshot will drift as TMDb
updates popularity and votes, so expect small changes on a fresh fetch.

## Adding a strategy

`DEFAULT_STRATEGIES` in `app/recommendation/evaluation.py` maps a name to a
callable `(user, dataset, k) → ranked ids or RankedItems`.
`make_ranking_strategy(weights=..., mmr_lambda=..., max_per_genre=...,
candidate_pool="production")` wraps the production ranker, and
`production_variant(**overrides)` is production with some weights — or caps
(`max_per_genre`, `max_per_director`, `max_per_collection`) — changed, so
changes can be measured, with paired intervals, before shipping:

```python
"more_dense": production_variant(similarity=0.6, sparse_positive=0.3),
```

or without code: `--variant more_dense:similarity=0.6,sparse_positive=0.3`.
A user's profile and per-candidate channels (`candidate_channels`) are computed
once and shared by every strategy, so a variant costs one blend and one slate
selection per user.
