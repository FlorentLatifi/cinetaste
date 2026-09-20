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
