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

## Results: synthetic benchmark

800 titles, 200 users, seed 7, held-out 3 positives each. Generated with
`build_synthetic_dataset`; reproduce with the commands above.

**k = 20 (the production slate size)**

| strategy | recall@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|
| random | 0.012 | 0.009 | 0.035 | 0.014 | 0.857 | 0.373 | 0.497 | 0.993 |
| popular | 0.022 | 0.010 | 0.065 | 0.010 | 0.872 | 0.399 | 0.012 | 0.028 |
| dense_only | 0.163 | 0.080 | 0.435 | 0.081 | 0.444 | 0.052 | 0.533 | 0.665 |
| sparse_only | 0.125 | 0.062 | 0.350 | 0.066 | 0.569 | 0.087 | 0.538 | 0.598 |
| hybrid_no_diversity | 0.163 | 0.081 | 0.455 | 0.084 | 0.480 | 0.062 | 0.538 | 0.569 |
| **hybrid (production)** | **0.088** | **0.052** | **0.255** | **0.066** | **0.741** | **0.199** | **0.552** | **0.436** |
| hybrid_with_exploration | 0.088 | 0.052 | 0.255 | 0.066 | 0.741 | 0.199 | 0.552 | 0.431 |

**k = 10**

| strategy | recall@10 | ndcg@10 | hit_rate@10 | diversity | genre_variety | coverage |
|---|---|---|---|---|---|---|
| random | 0.013 | 0.008 | 0.040 | 0.858 | 0.599 | 0.922 |
| popular | 0.007 | 0.005 | 0.020 | 0.873 | 0.707 | 0.015 |
| hybrid_no_diversity | 0.087 | 0.054 | 0.250 | 0.446 | 0.111 | 0.403 |
| **hybrid (production)** | 0.045 | 0.034 | 0.130 | 0.749 | 0.342 | 0.391 |

### What these numbers say

* **The taste model works.** Ranking by taste finds roughly 7× more held-out
  favourites than random (recall@20 0.163 vs 0.012) and reaches a held-out
  title for 45% of users in 20 cards.
* **Both channels contribute.** The content vector alone (0.163) beats the
  interpretable features alone (0.125); combining them matches the better one
  and ranks hits slightly higher (NDCG 0.081 vs 0.080). The sparse channel earns
  its place by making explanations possible, not by lifting accuracy much.
* **Diversity is expensive, and now the price is known.** MMR plus the genre cap
  cost about 46% of recall@20 (0.163 → 0.088) and buy 3.2× the genre variety
  (0.062 → 0.199) and noticeably less repetition (0.480 → 0.741). Without the
  cap a 20-card slate holds barely more than one distinct primary genre.
* **That measurement set the defaults.** A fixed cap of 4 per slate cost ~60% of
  recall@20; a cap proportional to slate size (40%, so 8 of 20) recovers most of
  it. λ moved 0.7 → 0.8 the same way.
* **Exploration slots are nearly free** at this slate size: identical accuracy,
  and they replace 3 near-duplicates with stretch picks.

### What this benchmark does *not* prove

* **It is partly circular.** Synthetic users are generated with preferences over
  the same kinds of features the recommender scores (genres, directors,
  keywords), so absolute accuracy is optimistic. Use it to compare strategies
  and catch regressions — not as evidence of real-world quality.
* **The popularity baseline is unfairly weak here.** Synthetic popularity is
  random noise, uncorrelated with taste. On real data, popularity is a strong
  baseline; that is the comparison that matters, and it needs MovieLens.
* **Offline metrics only reward re-finding what a user already rated.** They say
  nothing about whether a recommendation was a pleasant surprise, which is what
  exploration and hidden gems exist for. `recommendation_impressions` logs what
  each user was shown so that acting-on-recommendations can be measured once
  the app has real traffic.

## MovieLens (real ratings)

`--dataset movielens` runs the same protocol over
[MovieLens ml-latest-small](https://grouplens.org/datasets/movielens/)
(100k ratings, ~9k films, real people). Download and unzip it to
`data/ml-latest-small`; it is not committed (GroupLens forbids redistribution).

The split is temporal per user: their most recent 20% of ratings are held out,
and held-out titles rated ≥ 4 stars count as relevant. Caveat: MovieLens has no
cast, crew or synopses, so the people signals that carry much of the real
catalog's quality are absent — treat those numbers as a floor, and expect the
popularity baseline to be far stronger than it is on synthetic data.

> Not yet run here: the dataset needs downloading. The loader is tested and the
> command above produces the same table.

## Adding a strategy

`DEFAULT_STRATEGIES` in `app/recommendation/evaluation.py` maps a name to a
callable. `make_ranking_strategy(weights=..., mmr_lambda=..., max_per_genre=...)`
wraps the production ranker, so weight changes can be measured before shipping:

```python
"more_dense": make_ranking_strategy(
    weights=RankingWeights(similarity=0.6, sparse_positive=0.3, sparse_negative=0.1)
),
```
