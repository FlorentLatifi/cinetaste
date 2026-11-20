# Evaluating the recommender

Anyone can claim a recommender works. This is how CineTaste measures it, what
the numbers are today, and where they are weak.

```bash
cd backend
python -m app.scripts.evaluate_recommender                   # synthetic, no setup
python -m app.scripts.evaluate_recommender -k 20 --users 200 # production slate size
python -m app.scripts.evaluate_recommender --dataset movielens --path ../data/ml-latest-small
```

## Protocol

For every user:

1. Split their rated titles into **train** and **test** (held-out titles they
   rated positively).
2. Build the taste profile from the train events with the **production code
   path** — `effective_title_signals` → `build_profile`, the same functions the
   API calls. An evaluation that re-implements the model measures the
   re-implementation.
3. Rank the whole catalog with `rank_titles`, excluding everything in train.
4. Score the ranking against the held-out titles.

Every strategy runs through the same protocol, so the comparison is fair.

| Strategy | What it is |
|---|---|
| `random` | Shuffle the catalog. The floor. |
| `popular` | Most popular unseen titles. The baseline a recommender must beat to justify existing. |
| `dense_only` | Content-vector similarity only (no sparse features). |
| `sparse_only` | Interpretable feature overlap only (no vector). |
| `hybrid_no_diversity` | Production scoring, diversity controls off. |
| `hybrid` | **What production serves** (MMR λ=0.8, per-genre and per-director caps). |
| `hybrid_with_exploration` | Production plus 3 exploration slots. |
| `hybrid_plus_popularity` | Production plus a warm popularity prior (`warm_popularity=0.2`); not the default. |
| `hybrid_low_rating_penalty` | Production plus a soft penalty for ★ < 6.5 with enough votes (`low_rating=0.05`); not the default. |

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

**Trust** — two failures users notice immediately:

* `director_max_share` — largest share of one slate held by a single director
  (co-directed films count for each director). 0.10 = 2 of 20.
* `director_overflow` — share of slates with more than two titles by one director.
* `gems_per_slate` — titles labelled "hidden gem" per slate (capped at 3).
* `gem_pop_pct` / `gem_pop_pct_max` — mean / worst catalog popularity
  percentile of the labelled titles (1.0 = most popular). A hidden gem should
  sit low. Measured against the whole catalog; the ranker decides on the pool
  (catalog minus what the user rated), so values a hair above 0.40 are
  rounding between the two, not mislabels.

## Results: real ratings (MovieLens)

> These numbers predate the per-director cap and percentile-based hidden gems
> (November 2026) and have not been re-run since: the dataset was not available
> where that change was made. Re-run before relying on them.

193 users from [MovieLens ml-latest-small](https://grouplens.org/datasets/movielens/)
(100k ratings from real people), temporal split: each user's most recent 20% of
ratings held out, held-out titles rated ≥ 4 stars are relevant. Slate of 20.

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.001 | 0.001 | 0.026 | 0.006 | 0.724 | 0.351 | 0.511 | 0.329 |
| **popular** | **0.072** | **0.070** | **0.415** | **0.169** | 0.817 | 0.309 | 0.002 | 0.015 |
| dense_only | 0.015 | 0.011 | 0.109 | 0.032 | 0.132 | 0.071 | 0.402 | 0.109 |
| sparse_only | 0.036 | 0.039 | 0.347 | 0.120 | 0.720 | 0.256 | 0.142 | 0.049 |
| hybrid_no_diversity | 0.022 | 0.023 | 0.202 | 0.069 | 0.393 | 0.141 | 0.320 | 0.070 |
| **hybrid (production)** | 0.025 | 0.028 | 0.254 | 0.085 | 0.614 | 0.232 | 0.225 | 0.062 |
| hybrid_with_exploration | 0.025 | 0.028 | 0.249 | 0.084 | 0.610 | 0.233 | 0.232 | 0.065 |
| hybrid_plus_popularity | 0.049 | 0.050 | 0.404 | 0.127 | 0.711 | 0.242 | 0.044 | 0.045 |

### What real ratings say

* **Recommending the most popular films beats the recommender on accuracy** —
  about 3× the recall. This is the most important number in this document.
* **Why:** MovieLens gives only genres, user tags (on a minority of films) and a
  year — no cast, director or synopsis. With so little metadata, content-based
  ranking can barely tell films apart, while "what people rate next" in
  MovieLens is dominated by popular films. The production TMDb catalog carries
  directors, cast, writers, keywords and synopses, so this is a floor, not the
  ceiling — but it is a real warning: content alone is only as good as its
  metadata.
* **It changed the default weights.** The hashed content vector alone was the
  weakest signal here (0.015), the interpretable features the strongest
  (0.036). Moving weight from the vector to the features (similarity 0.42 → 0.2,
  feature overlap 0.40 → 0.6) raised the production ranker's hit rate from
  0.155 to 0.254, NDCG from 0.017 to 0.028 and doubled MRR on the same users —
  and did not hurt the synthetic benchmark (recall@20 0.088 → 0.095).
* **A popularity prior closes most of the gap — and was deliberately not made
  the default.** `hybrid_plus_popularity` (`warm_popularity=0.2`) nearly matches
  the popularity baseline's hit rate (0.404 vs 0.415), but its novelty falls to
  0.044: it mostly recommends the charts. For a product whose point is discovery,
  that trade isn't worth it by default; the knob exists
  (`RankingWeights.warm_popularity`) for anyone who wants it.
* **Diversity helps here, unlike on synthetic data** (0.025 vs 0.022 recall with
  diversity on): real users' held-out favourites span more genres than the
  synthetic users'.

Sampling noise is large at this size: a different random draw of 99 users gave
the old defaults 0.008 recall. Compare strategies on the *same* users (every row
above is), not across runs.

## Results: synthetic benchmark

800 titles, 200 users, seed 7, 3 held-out positives each, slate of 20.
Generated with `build_synthetic_dataset`.

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage | director_max_share | director_overflow | gems_per_slate | gem_pop_pct | gem_pop_pct_max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| random | 0.027 | 0.011 | 0.080 | 0.009 | 0.859 | 0.377 | 0.499 | 0.995 | 0.098 | 0.090 | 0.000 | – | – |
| popular | 0.022 | 0.010 | 0.065 | 0.010 | 0.872 | 0.399 | 0.012 | 0.028 | 0.100 | 0.000 | 0.000 | – | – |
| dense_only | 0.152 | 0.077 | 0.405 | 0.081 | 0.427 | 0.052 | 0.559 | 0.882 | 0.298 | 1.000 | 2.825 | 0.180 | 0.404 |
| sparse_only | 0.112 | 0.055 | 0.300 | 0.055 | 0.524 | 0.085 | 0.555 | 0.760 | 0.282 | 1.000 | 2.610 | 0.186 | 0.404 |
| hybrid_no_diversity | 0.135 | 0.066 | 0.360 | 0.066 | 0.490 | 0.077 | 0.569 | 0.806 | 0.296 | 1.000 | 2.790 | 0.181 | 0.404 |
| **hybrid (production)** | **0.090** | **0.047** | **0.245** | **0.051** | **0.759** | **0.192** | **0.680** | **0.659** | **0.100** | **0.000** | **3.000** | **0.158** | **0.390** |
| hybrid_with_exploration | 0.090 | 0.047 | 0.245 | 0.052 | 0.759 | 0.192 | 0.680 | 0.656 | 0.100 | 0.000 | 3.000 | 0.159 | 0.390 |
| hybrid_plus_popularity | 0.110 | 0.067 | 0.285 | 0.084 | 0.755 | 0.190 | 0.120 | 0.394 | 0.100 | 0.000 | 0.365 | 0.316 | 0.404 |
| hybrid_low_rating_penalty | 0.082 | 0.044 | 0.210 | 0.048 | 0.761 | 0.192 | 0.679 | 0.534 | 0.100 | 0.000 | 3.000 | 0.157 | 0.390 |

### What these numbers say

* **The taste model learns taste.** Ranking by taste finds 3–6× more held-out
  favourites than random or popular.
* **Diversity has a measured price here.** MMR plus the genre and director caps
  cost about a third of recall (0.135 → 0.090) and buy 2.5× the genre variety.
  Without the caps a 20-card slate holds barely more than one distinct primary
  genre and every slate has more than two films by one director. A fixed genre
  cap of 4 per slate cost ~60% of recall, which is why the genre cap is
  proportional to slate size (40%) and MMR λ is 0.8.
* **Popularity is noise in this dataset** (popularity and rating are drawn at
  random), so anything that reads them only reshuffles. The popularity prior
  costs novelty (0.680 → 0.120); it scores higher recall here mainly because it
  outweighs the hidden-gem bonus, which on this data is noise as well
  (production without the gem bonus: recall 0.112). Whether the gem bonus pays
  for itself can only be decided on real ratings.

### Per-director cap and percentile hidden gems (November 2026)

Production slates showed 6 of 20 cards by one director, and "hidden gem" on The
Godfather Part II, Memento, Alien and The Prestige: the gem rule used absolute
TMDb popularity (< 40), but most of our catalog sits below 40. Two changes:

* **Hidden gems and the cold-start prior use catalog percentiles.** A title is
  a gem when it is well rated (★ ≥ 7.2), has enough votes (≥ 50 and above the
  catalog's 10th percentile), and is less known than 60% of the catalog on
  *both* popularity and vote count. Vote count matters: The Silence of the
  Lambs has a low trending score but a huge audience. The cold-start prior is
  `0.22 × max(popularity pct, vote-count pct)` instead of `min(popularity/200, 0.22)`,
  so one trending outlier no longer flattens the rest of the scale. Percentiles
  are stored per title in `Title.extra["catalog_percentiles"]` by ingest and
  re-embed; titles without them fall back to percentiles within the candidate pool.
* **At most two titles per director in a 20-card slate** (10% of the slate,
  at least 1), enforced in MMR, exploration and backfill. The cap is relaxed
  only when nothing else can fill the slate.

Same data before and after (synthetic, slate of 20, production `hybrid`):

| seed | recall@20 | ndcg@20 | hit_rate@20 | novelty | director_max_share | director_overflow | gem_pop_pct | gem_pop_pct_max |
|---|---|---|---|---|---|---|---|---|
| 7 before | 0.095 | 0.047 | 0.265 | 0.551 | 0.147 | 0.710 | 0.476 | 0.894 |
| 7 after | 0.090 | 0.047 | 0.245 | 0.680 | 0.100 | 0.000 | 0.158 | 0.390 |
| 11 before | 0.078 | 0.045 | 0.205 | 0.538 | 0.163 | 0.795 | 0.465 | 0.900 |
| 11 after | 0.072 | 0.041 | 0.205 | 0.668 | 0.100 | 0.000 | 0.164 | 0.399 |
| 23 before | 0.107 | 0.060 | 0.275 | 0.569 | 0.158 | 0.790 | 0.428 | 0.874 |
| 23 after | 0.095 | 0.055 | 0.275 | 0.696 | 0.100 | 0.000 | 0.173 | 0.404 |
| 42 before | 0.095 | 0.058 | 0.265 | 0.551 | 0.161 | 0.780 | 0.440 | 0.854 |
| 42 after | 0.085 | 0.057 | 0.245 | 0.664 | 0.100 | 0.000 | 0.192 | 0.358 |

* **Director concentration is gone:** slates with more than two films by one
  director fell from 71–80% to 0%.
* **Gem labels land on obscure titles:** mean popularity percentile of labelled
  titles fell from ~0.45 to ~0.17, the worst case from ~0.89 to ~0.40.
* **The price is recall: −8.8% on average over four seeds** (0.094 → 0.086),
  NDCG −5%, hit rate −4%. The director cap accounts for it (seed 7: 0.097 with
  the cap off, 0.090 on); the percentile gem rule alone is neutral (0.095 →
  0.097). This benchmark rewards repetition by construction: synthetic users
  like a cluster where ~12 directors make all the films, so held-out favourites
  usually share a director with the training titles. At a slate of 10 (one per
  director) recall did not drop (0.033 → 0.035).
* **The low-rating penalty is off.** On this benchmark it cost 9% recall and
  14% hit rate (`hybrid_low_rating_penalty`), and the benchmark cannot show a
  benefit (ratings are random). It stays a documented knob
  (`RankingWeights.low_rating`) until real ratings say otherwise.

### What this benchmark does *not* prove

* **It is partly circular.** Synthetic users are generated with preferences over
  the same kinds of features the recommender scores, so accuracy is optimistic.
  Use it to compare strategies and catch regressions; use MovieLens for reality.
* **Offline metrics only reward re-finding what a user already rated.** They say
  nothing about whether a recommendation was a pleasant surprise, which is what
  exploration and hidden gems exist for. `recommendation_impressions` logs what
  each user was shown so acting-on-recommendations can be measured once the app
  has real traffic.

## Reproducing

```bash
cd backend
python -m app.scripts.evaluate_recommender -k 20 --users 200            # synthetic
python -m app.scripts.evaluate_recommender -k 20 --users 200     --dataset movielens --path ../data/ml-latest-small                   # real ratings
```

The evaluator imports the app's settings, so it needs `JWT_SECRET` and
`DATABASE_URL` set (any value; no database is contacted), as in CI.

MovieLens is not committed (GroupLens forbids redistribution): download
ml-latest-small and unzip it to `data/`. The MovieLens run takes ~15 minutes
(every strategy ranks ~9.7k films for every user).

## Adding a strategy

`DEFAULT_STRATEGIES` in `app/recommendation/evaluation.py` maps a name to a
callable. `make_ranking_strategy(weights=..., mmr_lambda=..., max_per_genre=...)`
wraps the production ranker, so weight changes can be measured before shipping:

```python
"more_dense": make_ranking_strategy(
    weights=RankingWeights(similarity=0.6, sparse_positive=0.3, sparse_negative=0.1)
),
```
