# Evaluating the recommender

Anyone can claim a recommender works. This is how CineTaste measures it, what
the numbers are today, and where they are weak.

```bash
cd backend
python -m app.scripts.evaluate_recommender                   # synthetic, no setup
python -m app.scripts.evaluate_recommender -k 20 --users 200 # production slate size
python -m app.scripts.evaluate_recommender --dataset movielens --path ../data/ml-latest-small --workers 4
```

## Protocol

For every user:

1. Split their rated titles into **train** and **test** (held-out titles they
   rated positively).
2. Build the taste profile from the train events with the **production code
   path** — `effective_title_signals` → `build_profile`, the same functions the
   API calls. An evaluation that re-implements the model measures the
   re-implementation.
3. Rank, excluding everything in train: the whole catalog for the ablations,
   or — for `production` — the same candidate pool the API ranks (nearest
   neighbours of the taste vector plus the most popular titles).
4. Score the ranking against the held-out titles.

Every strategy runs through the same protocol, so the comparison is fair.

**Title statistics come from training data only.** Popularity, average rating
and vote count are computed from every user's *train* portion. Until November
2026 they also counted the held-out ratings, which leaked the answers into the
popularity baseline and into every prior that reads ratings. The effect was
small (popular recall@20 0.072 → 0.070), but a benchmark should not have it;
`--title-stats all` reproduces the old protocol.

| Strategy | What it is |
|---|---|
| `random` | Shuffle the catalog. The floor. |
| `popular` | Most popular unseen titles. The baseline a recommender must beat to justify existing. |
| `dense_only` | Content-vector similarity only (no sparse features, no priors). |
| `sparse_only` | Interpretable feature overlap only (no vector, no priors). |
| `hybrid_no_diversity` | Production scoring, diversity controls (MMR, genre and director caps) off. |
| `hybrid_no_quality` | Production scoring without the quality prior. |
| `hybrid` | Production scoring and diversity, ranking the whole catalog. |
| `hybrid_with_exploration` | `hybrid` plus 3 exploration slots. |
| `hybrid_plus_popularity` | `hybrid` plus a popularity prior (`warm_popularity=0.2`). Not the default. |
| `production` | **What the API serves**: candidate generation (250 nearest + 120 popular), the production ranker, 3 exploration slots. |

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
Title statistics from training data only (November 2026 protocol).

| strategy | recall@20 | precision@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|---|
| random | 0.003 | 0.003 | 0.003 | 0.062 | 0.023 | 0.728 | 0.360 | 0.504 | 0.331 |
| **popular** | **0.070** | **0.047** | **0.067** | **0.415** | **0.168** | 0.811 | 0.285 | 0.002 | 0.014 |
| dense_only | 0.013 | 0.005 | 0.010 | 0.093 | 0.024 | 0.127 | 0.069 | 0.404 | 0.110 |
| sparse_only | 0.035 | 0.026 | 0.038 | 0.332 | 0.121 | 0.713 | 0.254 | 0.154 | 0.049 |
| hybrid_no_diversity | 0.026 | 0.019 | 0.032 | 0.295 | 0.106 | 0.474 | 0.167 | 0.223 | 0.062 |
| hybrid_no_quality | 0.023 | 0.015 | 0.024 | 0.238 | 0.075 | 0.594 | 0.231 | 0.282 | 0.068 |
| hybrid | 0.037 | 0.025 | 0.039 | 0.316 | 0.109 | 0.675 | 0.245 | 0.153 | 0.056 |
| hybrid_with_exploration | 0.036 | 0.024 | 0.038 | 0.311 | 0.106 | 0.670 | 0.255 | 0.164 | 0.059 |
| hybrid_plus_popularity | 0.057 | 0.038 | 0.056 | 0.435 | 0.149 | 0.735 | 0.255 | 0.045 | 0.045 |
| **production** | **0.043** | **0.033** | **0.045** | **0.352** | **0.127** | 0.628 | 0.232 | 0.139 | 0.057 |

Before the November 2026 changes, on the same users and protocol, production
scored recall 0.030, precision 0.019, NDCG 0.028, hit rate 0.259, MRR 0.078,
diversity 0.532, novelty 0.273, coverage 0.081 (see
[QUALITY_AUDIT.md](QUALITY_AUDIT.md) §4). The table published before that —
production recall 0.025, hit rate 0.254 — was reproduced exactly; it used the
old protocol and ranked the whole catalog.

### What real ratings say

* **Recommending the most popular films still beats the recommender on
  accuracy** — production now reaches 61% of its recall and 85% of its hit
  rate, at 70× its novelty and 4× its coverage.
* **Why it loses, measured rather than guessed:**
  * MovieLens gives only genres, user tags (16% of films) and a year — no
    cast, director or synopsis. Its genre lists are alphabetical in 99% of
    rows, so the "primary genre" the diversity cap relies on is arbitrary here.
  * Consequently **74% of the catalog shares an identical feature snapshot with
    at least one other film** (the largest group holds 241). Content cannot
    tell those apart; ties used to fall to catalog order, mostly obscure films.
    That read as "novelty" and carried no signal.
  * MovieLens asks people to rate popular films; the top 5% of films hold 43%
    of all ratings. "What will this user rate next" structurally favours
    popularity.
  * Leakage existed but was small (above).
* **The quality prior is the fix that serves both the benchmark and the
  product.** It ranks films people rate well (a vote-count-shrunk average,
  as a pool percentile) above films people merely know. Against the same
  ranker without it (`hybrid_no_quality`): recall +61%, NDCG +63%, MRR +45%,
  diversity +14%. Against a popularity prior on the same users: at equal
  novelty (~0.155) quality gives 42% more recall (0.037 vs 0.026), and
  popularity needs weight 0.08 to approach its NDCG — by which point novelty
  has fallen to 0.091 (it does edge quality on hit rate, 0.332 vs 0.316). So
  quality is the default and `warm_popularity` stays off.
* **Candidate generation helps.** Ranking the production pool (`production`)
  beats ranking the whole catalog (`hybrid`): the popular slice gives the
  ranker well-known films to weigh, the neighbour slice keeps it on taste.
* **The interpretable features remain the strongest content signal**
  (`sparse_only` 0.035 vs `dense_only` 0.013), which is why the score weights
  them 0.6 and the hashed vector 0.2.

Sampling noise is large at this size: a different random draw of 99 users gave
the old defaults 0.008 recall. Compare strategies on the *same* users (every row
above is), not across runs.

## Results: synthetic benchmark

800 titles, 200 users, seed 7, 3 held-out positives each, slate of 20.
Generated with `build_synthetic_dataset`.

| strategy | recall@20 | precision@20 | ndcg@20 | hit_rate@20 | mrr | diversity | genre_variety | novelty | coverage |
|---|---|---|---|---|---|---|---|---|---|
| random | 0.027 | 0.004 | 0.011 | 0.080 | 0.009 | 0.859 | 0.377 | 0.499 | 0.995 |
| popular | 0.022 | 0.003 | 0.010 | 0.065 | 0.010 | 0.872 | 0.399 | 0.012 | 0.028 |
| dense_only | 0.157 | 0.024 | 0.084 | 0.430 | 0.098 | 0.429 | 0.052 | 0.543 | 0.877 |
| sparse_only | 0.108 | 0.016 | 0.054 | 0.290 | 0.057 | 0.526 | 0.087 | 0.547 | 0.752 |
| hybrid_no_diversity | 0.133 | 0.020 | 0.065 | 0.355 | 0.067 | 0.536 | 0.085 | 0.552 | 0.604 |
| hybrid_no_quality | 0.097 | 0.015 | 0.052 | 0.245 | 0.057 | 0.760 | 0.193 | 0.638 | 0.667 |
| hybrid | 0.093 | 0.014 | 0.051 | 0.255 | 0.056 | 0.765 | 0.199 | 0.637 | 0.421 |
| hybrid_with_exploration | 0.093 | 0.014 | 0.050 | 0.255 | 0.055 | 0.765 | 0.199 | 0.637 | 0.420 |
| hybrid_plus_popularity | 0.100 | 0.015 | 0.053 | 0.270 | 0.058 | 0.764 | 0.205 | 0.180 | 0.361 |
| **production** | **0.097** | **0.015** | **0.051** | **0.255** | **0.052** | **0.760** | **0.194** | **0.582** | **0.398** |

Before the November 2026 changes production scored recall 0.093, NDCG 0.047,
hit rate 0.265, MRR 0.048, diversity 0.749, novelty 0.549, coverage 0.420.

### What these numbers say

* **The taste model learns taste.** Ranking by taste finds 3.5–6× more held-out
  favourites than random or popular.
* **Diversity has a measured price here.** MMR plus the genre and director
  caps cost about 30% of recall (0.133 → 0.093) and buy over twice the genre
  variety.
  Without the cap a 20-card slate holds barely more than one distinct primary
  genre. A fixed cap of 4 cost ~60% of recall, which is why the cap is
  proportional to slate size (40%) and MMR λ is 0.8.
* **The director cap costs ~8% of recall on this benchmark** (0.105 → 0.097 in
  isolation). Synthetic users are *generated* from director clusters, so this
  benchmark rewards exactly the auteur bubble the cap prevents — on the live
  site one taste profile produced eight films by one director in the first
  nine cards. A deliberate product trade.
* **The new hidden-gem rule and the director cap raised coverage** (0.425 →
  0.667, before the quality prior): gems are now the least-seen well-rated
  titles of the pool.
* **Ratings are random noise in this dataset**, so the quality prior neither
  helps nor hurts accuracy here; it narrows coverage back to its old level.

### What this benchmark does *not* prove

* **It is partly circular.** Synthetic users are generated with preferences over
  the same kinds of features the recommender scores, so accuracy is optimistic.
  Use it to compare strategies and catch regressions; use MovieLens for reality.
* **Offline metrics only reward re-finding what a user already rated.** They say
  nothing about whether a recommendation was a pleasant surprise, which is what
  exploration and hidden gems exist for. `recommendation_impressions` logs what
  each user was shown so acting-on-recommendations can be measured once the app
  has real traffic.

## Online: what people do with their picks

Offline metrics cannot value a pleasant surprise; real behaviour can. Every
computed For You slate is logged to `recommendation_impressions`, every action
to `interaction_events`, and `app/application/engagement.py` joins them:

```bash
cd backend
python -m app.scripts.engagement_report                                # last 30 days
python -m app.scripts.engagement_report --since 2026-11-01 --until 2026-12-01
```

In production run the **Engagement report** workflow (Actions, manual): it uses
the Catalog workflow's `DATABASE_URL` secret, is read-only, and writes the
tables to the job summary.

**Method.** One row per user and title — its *first* impression in the period
(a title shown again on the next visit is the same recommendation). Its outcome
is the first answer within `--window-days` (default 7):

| outcome | events | reads as |
|---|---|---|
| saved | `watchlist` | the discovery success: wants to watch it |
| seen_liked | `rate_2`–`rate_4`, `like`, `watched_liked` | a right guess the person had already found |
| seen_other | `mid`, `watched` | seen, no strong opinion |
| passed | `not_interested`, `dislike`, `rate_1`, `watched_disliked` | wrong for them |

`clear`, `skip`, `view` and `haven't_seen` are not answers. `good` = saved +
seen_liked; `good_of_acted` is the same among picks that got any answer.
Rates are reported overall, by position band and by reason code (`hidden_gem`,
`discovery`, `because_you_liked`, …); groups under 20 picks show a count only.

**Reading it honestly.**
* Every card of a computed slate is logged, swiped to or not, so later
  positions look worse partly because fewer people reached them. Compare
  reason codes within a position band before concluding a reason "works".
* Impressions carry no ranker version: compare configurations by date range,
  before and after a deploy, over similar lengths of time.
* `seen_liked` is accuracy, `saved` is discovery. A change that trades one for
  the other is a product decision, not a regression.
* Early on, most groups will be too small. That is the honest answer.

## Reproducing

```bash
cd backend
python -m app.scripts.evaluate_recommender -k 20 --users 200 --workers 4      # synthetic
python -m app.scripts.evaluate_recommender -k 20 --users 200 --workers 4 \
    --dataset movielens --path ../data/ml-latest-small                     # real ratings
```

MovieLens is not committed (GroupLens forbids redistribution): download
ml-latest-small and unzip it to `data/`. The MovieLens run takes ~12 minutes
on one core and ~6 with `--workers 4` (every strategy ranks ~9.7k films for
every user); results are identical either way.

## Adding a strategy

`DEFAULT_STRATEGIES` in `app/recommendation/evaluation.py` maps a name to a
callable. `make_ranking_strategy(weights=..., mmr_lambda=..., max_per_genre=...,
max_per_director=...)` wraps the production ranker over the whole catalog;
`make_production_strategy(weights=...)` adds production candidate generation.
Weight changes can be measured before shipping:

```python
"more_dense": make_ranking_strategy(
    weights=RankingWeights(similarity=0.6, sparse_positive=0.3, sparse_negative=0.1)
),
```
