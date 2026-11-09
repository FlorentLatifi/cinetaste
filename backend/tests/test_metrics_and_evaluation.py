"""Ranking metrics (hand-checked) and the offline evaluation harness."""

from __future__ import annotations

import math

import pytest

from app.recommendation import metrics as M
from app.recommendation.eval_datasets import build_synthetic_dataset
from app.recommendation.evaluation import evaluate
from app.recommendation.pipeline import genre_cap_for


def test_precision_recall_and_hit_rate() -> None:
    recommended = ["a", "b", "c", "d"]
    relevant = {"b", "d", "z"}
    assert M.precision_at_k(recommended, relevant, k=4) == pytest.approx(0.5)
    assert M.precision_at_k(recommended, relevant, k=2) == pytest.approx(0.5)
    assert M.recall_at_k(recommended, relevant, k=4) == pytest.approx(2 / 3)
    assert M.hit_rate_at_k(recommended, relevant, k=2) == 1.0
    assert M.hit_rate_at_k(recommended, {"z"}, k=4) == 0.0
    assert M.recall_at_k(recommended, set(), k=4) == 0.0


def test_reciprocal_rank_uses_first_hit() -> None:
    assert M.reciprocal_rank(["a", "b", "c"], {"b", "c"}) == pytest.approx(0.5)
    assert M.reciprocal_rank(["a", "b"], {"z"}) == 0.0


def test_ndcg_rewards_earlier_hits() -> None:
    early = M.ndcg_at_k(["hit", "x", "y"], {"hit"}, k=3)
    late = M.ndcg_at_k(["x", "y", "hit"], {"hit"}, k=3)
    assert early == pytest.approx(1.0)
    assert late == pytest.approx(1 / math.log2(4))
    assert late < early


def test_ndcg_supports_graded_relevance() -> None:
    # Putting the 2-point title first is ideal; swapping them scores lower.
    gains = {"loved": 2.0, "liked": 1.0}
    best = M.ndcg_at_k(["loved", "liked"], gains, k=2)
    worse = M.ndcg_at_k(["liked", "loved"], gains, k=2)
    assert best == pytest.approx(1.0)
    assert worse < best


def test_intra_list_diversity_bounds() -> None:
    same = [[1.0, 0.0], [1.0, 0.0]]
    orthogonal = [[1.0, 0.0], [0.0, 1.0]]
    assert M.intra_list_diversity(same) == pytest.approx(0.0, abs=1e-9)
    assert M.intra_list_diversity(orthogonal) == pytest.approx(1.0, abs=1e-9)
    assert M.intra_list_diversity([[1.0, 0.0]]) == 0.0


def test_novelty_and_coverage() -> None:
    ranks = {"blockbuster": 0.0, "midlist": 0.5, "obscure": 1.0}
    assert M.novelty(["blockbuster", "obscure"], ranks) == pytest.approx(0.5)
    assert M.catalog_coverage([["a", "b"], ["b", "c"]], catalog_size=10) == pytest.approx(0.3)
    assert M.distinct_ratio(["drama", "drama", "comedy"]) == pytest.approx(2 / 3)


def test_genre_cap_scales_with_slate_size() -> None:
    assert genre_cap_for(20) == 8
    assert genre_cap_for(10) == 4
    assert genre_cap_for(3) == 3  # never below the floor


def test_recommender_beats_popularity_and_random_on_synthetic_data() -> None:
    """Smoke test for the harness and a floor for ranking quality."""
    dataset = build_synthetic_dataset(n_titles=180, n_users=25, seed=3)
    results = evaluate(dataset, k=10)

    hybrid = results["hybrid"]["recall@10"]
    assert hybrid > results["random"]["recall@10"] * 3
    assert hybrid > results["popular"]["recall@10"]
    # Diversity controls cost accuracy and buy variety — both sides measurable.
    assert results["hybrid"]["genre_variety"] > results["hybrid_no_diversity"]["genre_variety"]
    assert results["hybrid_no_diversity"]["recall@10"] >= hybrid
    # Popularity-only recommendations are by definition not novel.
    assert results["popular"]["novelty"] < results["hybrid"]["novelty"]


def test_parallel_evaluation_matches_serial() -> None:
    """--workers is a speed-up only: every strategy is deterministic per user."""
    from app.recommendation.evaluation import (
        make_production_strategy,
        make_ranking_strategy,
        popularity_strategy,
    )

    dataset = build_synthetic_dataset(n_titles=120, n_users=12, seed=3)
    strategies = {
        "popular": popularity_strategy,
        "hybrid": make_ranking_strategy(mmr_lambda=0.8),
        "production": make_production_strategy(ann_candidates=40, popular_candidates=20),
    }
    serial = evaluate(dataset, strategies, k=10, workers=1)
    parallel = evaluate(dataset, strategies, k=10, workers=2)
    assert serial == parallel


def _write_movielens(tmp_path) -> None:
    """Two users, three films. User 1's last rating (film 30) is held out."""
    (tmp_path / "movies.csv").write_text(
        "movieId,title,genres\n"
        "10,Old Favourite (1990),Drama\n"
        "20,Other Film (1995),Comedy\n"
        "30,Held Out (2000),Drama\n",
        encoding="utf-8",
    )
    rows = ["userId,movieId,rating,timestamp"]
    # user 1: 10 ratings of film 10/20 alternating, then film 30 last (held out)
    for i in range(10):
        rows.append(f"1,{10 if i % 2 == 0 else 20},4.0,{1000 + i}")
    rows.append("1,30,5.0,2000")
    # user 2: rates film 30 early (train) — that rating may count
    rows.append("2,30,4.0,500")
    for i in range(9):
        rows.append(f"2,{10 if i % 2 == 0 else 20},3.0,{600 + i}")
    (tmp_path / "ratings.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_movielens_title_stats_exclude_held_out_ratings(tmp_path) -> None:
    """Held-out ratings must not inflate the popularity they are scored against."""
    from app.recommendation.eval_datasets import load_movielens_dataset

    _write_movielens(tmp_path)
    clean = load_movielens_dataset(tmp_path, min_ratings=5, max_users=None, test_fraction=0.1)
    leaky = load_movielens_dataset(
        tmp_path, min_ratings=5, max_users=None, test_fraction=0.1, title_stats="all"
    )
    held_out_clean = next(t for t in clean.titles if t.name == "Held Out")
    held_out_leaky = next(t for t in leaky.titles if t.name == "Held Out")
    # Only user 2's training rating counts; user 1's held-out 5-star does not.
    assert held_out_clean.vote_count == 1
    assert held_out_leaky.vote_count == 2
    assert held_out_clean.vote_average == 8.0
