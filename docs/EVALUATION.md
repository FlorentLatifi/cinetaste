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
| `hybrid` | **What production serves** (MMR λ=0.8, per-genre cap). |
| `hybrid_with_exploration` | Production plus 3 exploration slots. |

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

## Results: real ratings (MovieLens)

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

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.027 | 0.011 | 0.080 | 0.009 | 0.859 | 0.377 | 0.499 | 0.995 |
| popular | 0.022 | 0.010 | 0.065 | 0.010 | 0.872 | 0.399 | 0.012 | 0.028 |
| dense_only | 0.163 | 0.080 | 0.435 | 0.081 | 0.444 | 0.052 | 0.533 | 0.665 |
| sparse_only | 0.125 | 0.062 | 0.350 | 0.066 | 0.569 | 0.087 | 0.538 | 0.598 |
| hybrid_no_diversity | 0.143 | 0.071 | 0.395 | 0.072 | 0.529 | 0.077 | 0.538 | 0.542 |
| **hybrid (production)** | **0.095** | **0.047** | **0.265** | **0.048** | **0.750** | **0.186** | **0.551** | **0.425** |
| hybrid_with_exploration | 0.095 | 0.047 | 0.265 | 0.048 | 0.751 | 0.186 | 0.551 | 0.420 |
| hybrid_plus_popularity | 0.098 | 0.057 | 0.260 | 0.071 | 0.762 | 0.195 | 0.193 | 0.357 |

### What these numbers say

* **The taste model learns taste.** Ranking by taste finds 3.5–6× more held-out
  favourites than random or popular.
* **Diversity has a measured price here.** MMR plus the genre cap cost about a
  third of recall (0.143 → 0.095) and buy 2.4× the genre variety. Without the cap
  a 20-card slate holds barely more than one distinct primary genre. A fixed cap
  of 4 per slate cost ~60% of recall, which is why the cap is now proportional to
  slate size (40%) and MMR λ is 0.8.
* **Popularity is noise in this dataset**, so the popularity prior only costs
  novelty (0.551 → 0.193) without a meaningful accuracy gain.

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
