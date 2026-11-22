"""feature_order: the main genre first, from the stored snapshot, never a crash."""

from __future__ import annotations

from app.recommendation.embeddings import feature_order

NAMES = ["Action", "Mystery", "Thriller"]  # as the database lists them


def test_the_heaviest_feature_leads_and_ties_keep_their_order() -> None:
    extra = {"feature_snapshot": {"genre:thriller": 1.0, "genre:action": 0.7, "genre:mystery": 0.7}}
    assert sorted(NAMES, key=feature_order(extra, "genre:")) == ["Thriller", "Action", "Mystery"]


def test_kept_keywords_come_before_dropped_ones() -> None:
    extra = {"feature_snapshot": {"kw:zeppelin": 0.9}}
    assert sorted(["alibi", "zeppelin"], key=feature_order(extra, "kw:")) == ["zeppelin", "alibi"]


def test_without_a_usable_snapshot_the_order_is_unchanged() -> None:
    for extra in (None, {}, {"feature_snapshot": None}, {"feature_snapshot": ["genre:action"]}):
        assert sorted(NAMES, key=feature_order(extra, "genre:")) == NAMES
    bad_weight = {"feature_snapshot": {"genre:thriller": "heavy"}}
    assert sorted(NAMES, key=feature_order(bad_weight, "genre:")) == NAMES
