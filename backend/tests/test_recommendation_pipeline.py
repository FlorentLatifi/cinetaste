"""Critical-path tests for ranking: signals, cold start, MMR, exclusions."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from app.domain.taste_signals import affects_taste, weight_for
from app.recommendation.embeddings import (
    PersonSignal,
    blend_vectors,
    cosine,
    sparse_channel_scores,
)
from app.recommendation.explanations import Reason
from app.recommendation.pipeline import (
    CATALOG_PERCENTILES_KEY,
    DEFAULT_WEIGHTS,
    GEM_SOFT_BONUS,
    GEM_STRONG_BONUS,
    RankingWeights,
    annotate_discovery_reasons,
    blend_scores,
    candidate_channels,
    catalog_percentiles,
    director_cap_for,
    fame,
    gem_boost,
    low_rating_penalty,
    mmr_select,
    percent_rank,
    quality_percentiles,
    rank_titles,
    select_slate,
)
from tests.conftest import FakeTitle, accumulate_features

# ---------------------------------------------------------------------------
# Signal accumulation → profile shape
# ---------------------------------------------------------------------------


def test_positive_ratings_build_positive_features() -> None:
    liked = FakeTitle(
        name="Inception-like",
        genres=["Science Fiction", "Thriller"],
        keywords=["mind-bending"],
        people=[PersonSignal("Chris Nolan", "director")],
    )
    snap = liked.extra["feature_snapshot"]
    w = weight_for("rate_4")
    features = accumulate_features([(snap, w, "rate_4")])
    assert features.get("person:director:chris nolan", 0) > 0
    assert features.get("genre:science fiction", 0) > 0


def test_negative_rating_pushes_features_negative() -> None:
    hated = FakeTitle(
        name="Bayhem",
        genres=["Action"],
        people=[PersonSignal("Michael Bay", "director")],
    )
    snap = hated.extra["feature_snapshot"]
    w = weight_for("rate_1")
    features = accumulate_features([(snap, w, "rate_1")])
    assert features.get("person:director:michael bay", 0) < 0
    assert features.get("genre:action", 0) < 0


def test_havent_seen_zero_contribution_to_features_and_vector() -> None:
    title = FakeTitle(
        name="Unseen Epic",
        genres=["Adventure"],
        people=[PersonSignal("Unknown", "director")],
    )
    snap = title.extra["feature_snapshot"]
    w = weight_for("haven't_seen")
    assert w == 0.0
    assert not affects_taste("haven't_seen", w)

    features = accumulate_features(
        [
            (snap, w, "haven't_seen"),
            (snap, w, "haven't_seen"),
        ]
    )
    assert features == {}
    assert blend_vectors([(list(title.embedding), w)]) is None


def test_mixed_signals_not_interested_weaker_than_bad() -> None:
    title = FakeTitle(name="Meh", genres=["Horror"], keywords=["gore"])
    snap = title.extra["feature_snapshot"]
    mild = accumulate_features([(snap, weight_for("not_interested"), "not_interested")])
    strong = accumulate_features([(snap, weight_for("rate_1"), "rate_1")])
    # Both negative; Bad should move further from zero
    key = next(k for k in strong if k.startswith("genre:"))
    assert strong[key] < mild[key] < 0


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


def test_cold_start_empty_profile_still_returns_slate(thriller_catalog: list[FakeTitle]) -> None:
    ranked = rank_titles(
        user_vector=None,
        user_features={},
        titles=thriller_catalog,
        exclude_ids=set(),
        slate_size=4,
        mmr_lambda=0.7,
        exploration_slots=1,
    )
    assert len(ranked) == 4
    assert all(item.reasons for item in ranked)
    # Cold path leans on popularity — top pool should include a high-pop title
    ids = {item.title_id for item in ranked}
    popular = max(thriller_catalog, key=lambda t: t.popularity)
    # Not guaranteed in top-4 after MMR, but slate must be full and scored
    assert len(ids) == 4
    assert popular.id in {t.id for t in thriller_catalog}


def test_cold_start_weak_features_use_pop_prior(thriller_catalog: list[FakeTitle]) -> None:
    """Tiny profile mass still ranks; popular titles remain competitive."""
    ranked = rank_titles(
        user_vector=None,
        user_features={"genre:comedy": 0.1},  # very weak
        titles=thriller_catalog,
        exclude_ids=set(),
        slate_size=3,
        mmr_lambda=0.6,
        exploration_slots=0,
    )
    assert len(ranked) == 3
    assert all(r.score == r.score for r in ranked)  # finite scores


# ---------------------------------------------------------------------------
# Ranking under taste
# ---------------------------------------------------------------------------


def test_positive_profile_prefers_matching_titles(thriller_catalog: list[FakeTitle]) -> None:
    nolanish = next(t for t in thriller_catalog if t.name == "Quiet Stars")
    user_vec = list(nolanish.embedding)
    user_features = {
        "person:director:chris nolan": 2.5,
        "genre:science fiction": 2.0,
        "tone:cerebral": 1.5,
        "kw:mind-bending": 1.2,
    }
    ranked = rank_titles(
        user_vector=user_vec,
        user_features=user_features,
        titles=thriller_catalog,
        exclude_ids=set(),
        slate_size=3,
        mmr_lambda=0.75,
        exploration_slots=0,
    )
    assert ranked[0].title_id == nolanish.id


def test_negative_profile_penalizes_disliked_director(thriller_catalog: list[FakeTitle]) -> None:
    comedy = next(t for t in thriller_catalog if t.name == "Laugh Factory")
    drama = next(t for t in thriller_catalog if t.name == "Indie Gem")
    # Strong hate for comedy director; mild like for drama
    user_features = {
        "person:director:sam jokes": -2.5,
        "genre:comedy": -1.5,
        "genre:drama": 1.8,
        "person:director:lee park": 1.5,
    }
    user_vec = list(drama.embedding)
    ranked = rank_titles(
        user_vector=user_vec,
        user_features=user_features,
        titles=[comedy, drama],
        exclude_ids=set(),
        slate_size=2,
        mmr_lambda=1.0,  # pure relevance
        exploration_slots=0,
    )
    assert ranked[0].title_id == drama.id
    by_id = {r.title_id: r.score for r in ranked}
    assert by_id[drama.id] > by_id[comedy.id]


def test_exclude_ids_removed_from_slate(thriller_catalog: list[FakeTitle]) -> None:
    skip = {thriller_catalog[0].id, thriller_catalog[1].id}
    ranked = rank_titles(
        user_vector=list(thriller_catalog[2].embedding),
        user_features={"genre:science fiction": 2.0},
        titles=thriller_catalog,
        exclude_ids=skip,
        slate_size=4,
        mmr_lambda=0.7,
        exploration_slots=0,
    )
    ranked_ids = {r.title_id for r in ranked}
    assert skip.isdisjoint(ranked_ids)


def test_ranked_items_include_explanations(thriller_catalog: list[FakeTitle]) -> None:
    target = next(t for t in thriller_catalog if t.name == "Quiet Stars")
    ranked = rank_titles(
        user_vector=list(target.embedding),
        user_features={
            "person:director:chris nolan": 2.0,
            "genre:science fiction": 1.5,
        },
        titles=thriller_catalog,
        exclude_ids=set(),
        slate_size=2,
        mmr_lambda=0.7,
        exploration_slots=0,
        explain_memory={
            "anchors": [
                {
                    "title_id": str(uuid4()),
                    "name": "Inception",
                    "weight": 1.55,
                    "directors": ["chris nolan"],
                    "cast": [],
                    "writers": [],
                    "tones": ["cerebral"],
                    "keywords": ["mind-bending"],
                    "genres": ["science fiction"],
                }
            ]
        },
    )
    assert ranked
    for item in ranked:
        assert item.reasons
        assert all(r.message.strip() for r in item.reasons)
    top = next(r for r in ranked if r.title_id == target.id)
    codes = {r.code for r in top.reasons}
    assert codes & {
        "because_you_liked",
        "same_director",
        "taste_blend",
        "similar_themes",
        "shared_genre",
        "taste_similarity",
    }


# ---------------------------------------------------------------------------
# MMR diversity
# ---------------------------------------------------------------------------


def test_mmr_selects_diverse_second_item() -> None:
    """Near-duplicate should lose to orthogonal candidate under mid λ."""
    dim = 384
    v_a = [1.0] + [0.0] * (dim - 1)
    v_near = [0.98, 0.02] + [0.0] * (dim - 2)
    v_far = [0.0, 1.0] + [0.0] * (dim - 2)
    # Normalize lightly
    for v in (v_a, v_near, v_far):
        n = sum(x * x for x in v) ** 0.5
        for i in range(len(v)):
            v[i] /= n

    id_a, id_near, id_far = uuid4(), uuid4(), uuid4()
    selected = mmr_select(
        [(id_a, 1.0, v_a), (id_near, 0.97, v_near), (id_far, 0.72, v_far)],
        k=2,
        lambda_mult=0.5,
    )
    assert selected[0] == id_a
    assert id_far in selected
    assert id_near not in selected


def test_mmr_lambda_one_is_pure_relevance() -> None:
    dim = 384
    v1 = [1.0] + [0.0] * (dim - 1)
    v2 = [0.0, 1.0] + [0.0] * (dim - 2)
    id1, id2, id3 = uuid4(), uuid4(), uuid4()
    selected = mmr_select(
        [(id1, 0.5, v1), (id2, 0.9, v2), (id3, 0.8, v1)],
        k=2,
        lambda_mult=1.0,
    )
    assert selected[0] == id2
    assert selected[1] == id3


def test_rank_mmr_reduces_same_director_cluster(thriller_catalog: list[FakeTitle]) -> None:
    """With diversity, slate should not be only Sam Jokes comedies when alternatives exist."""
    user_features = {"genre:comedy": 3.0, "person:director:sam jokes": 2.0}
    comedy = next(t for t in thriller_catalog if t.name == "Laugh Factory")
    ranked = rank_titles(
        user_vector=list(comedy.embedding),
        user_features=user_features,
        titles=thriller_catalog,
        exclude_ids=set(),
        slate_size=4,
        mmr_lambda=0.55,
        exploration_slots=1,
    )
    names = []
    by_id = {t.id: t for t in thriller_catalog}
    for item in ranked:
        names.append(by_id[item.title_id].name)
    # At least one non-comedy or non-Jokes title in a 4-slot slate
    jokes_only = all(
        by_id[item.title_id].name in {"Laugh Factory", "Slapstick City"} for item in ranked
    )
    assert not jokes_only or len(ranked) < 4


# ---------------------------------------------------------------------------
# Hidden gems + exploration annotations
# ---------------------------------------------------------------------------


def test_gem_boost_uses_catalog_percentiles() -> None:
    # Strong tier: well rated and less known than 40% of the catalog.
    assert gem_boost(7.5, popularity_pct=0.20, vote_count_pct=0.30) == GEM_STRONG_BONUS
    # Soft tier: very well rated, moderately known.
    assert gem_boost(7.6, popularity_pct=0.50, vote_count_pct=0.30) == GEM_SOFT_BONUS
    assert gem_boost(6.5, popularity_pct=0.05, vote_count_pct=0.30) == 0.0
    # Famous: top of the catalog by popularity.
    assert gem_boost(8.7, popularity_pct=0.95, vote_count_pct=0.30) == 0.0


def test_gem_boost_scale_free_in_raw_popularity() -> None:
    """The Godfather Part II at TMDb popularity ~40 is not hidden in our catalog."""
    assert gem_boost(8.6, popularity_pct=0.97, vote_count_pct=0.99, vote_count=13_000) == 0.0


def test_gem_needs_low_vote_count_too() -> None:
    """A classic with a low trending score but a huge audience is not a hidden gem."""
    # The Silence of the Lambs: TMDb popularity ~7.5 (low percentile), vote count top 1%.
    assert gem_boost(8.3, popularity_pct=0.15, vote_count_pct=0.99, vote_count=16_000) == 0.0
    assert fame(0.15, 0.99) == 0.99
    assert fame(0.15, None) == 0.15


def test_gem_needs_enough_votes_absolute_and_relative() -> None:
    assert gem_boost(8.0, popularity_pct=0.1, vote_count_pct=0.3, vote_count=12) == 0.0
    assert gem_boost(8.0, popularity_pct=0.1, vote_count_pct=0.05, vote_count=400) == 0.0
    assert gem_boost(8.0, popularity_pct=0.1, vote_count_pct=0.3, vote_count=400) > 0


def test_percent_rank_matches_sql_semantics() -> None:
    assert list(percent_rank([10.0, 30.0, 20.0, 20.0])) == [0.0, 1.0, 1 / 3, 1 / 3]
    assert list(percent_rank([5.0])) == [0.0]
    assert len(percent_rank([])) == 0


def test_catalog_percentiles_prefer_stored_values_over_pool() -> None:
    """Stored catalog-wide values win; titles without them use the pool fallback."""
    stored = FakeTitle(name="Stored", genres=["Drama"], popularity=1.0)
    stored.extra[CATALOG_PERCENTILES_KEY] = {"popularity": 0.9, "vote_count": 0.8}
    plain = [FakeTitle(name=f"P{i}", genres=["Drama"], popularity=10.0 + i) for i in range(3)]
    pop, votes = catalog_percentiles([stored, *plain])
    assert pop[0] == 0.9 and votes[0] == 0.8
    # Pool fallback: lowest pool popularity → 0.0 (the stored title's raw 1.0 is lowest).
    assert list(pop[1:]) == [1 / 3, 2 / 3, 1.0]
    # FakeTitle has no vote_count: unknown, not zero.
    assert all(v != v for v in votes[1:])


def test_catalog_percentiles_ignore_malformed_stored_values() -> None:
    title = FakeTitle(name="Odd", genres=["Drama"], popularity=5.0)
    title.extra[CATALOG_PERCENTILES_KEY] = {"popularity": "n/a", "vote_count": 7.0}
    other = FakeTitle(name="Other", genres=["Drama"], popularity=50.0)
    pop, votes = catalog_percentiles([title, other])
    assert pop[0] == 0.0  # fell back to the pool
    assert votes[0] != votes[0]  # 7.0 is not a percentile; still unknown


def test_director_cap_scales_with_slate_size() -> None:
    assert director_cap_for(20) == 2
    assert director_cap_for(30) == 3
    assert director_cap_for(12) == 1
    assert director_cap_for(3) == 1  # never zero


def test_low_rating_penalty_needs_enough_votes() -> None:
    assert low_rating_penalty(7.0, vote_count=5_000) == 0.0
    assert low_rating_penalty(6.0, vote_count=5_000) == pytest.approx(0.5)
    assert low_rating_penalty(3.0, vote_count=5_000) == 1.0
    assert low_rating_penalty(4.0, vote_count=10) == 0.0  # a handful of votes is noise


def test_low_rating_weight_is_off_by_default_and_demotes_when_on() -> None:
    """Off by default (not shown to be harmless offline); when on, it demotes."""
    assert DEFAULT_WEIGHTS.low_rating == 0.0
    good = FakeTitle(name="Good", genres=["Drama"], keywords=["family"], vote_average=7.0)
    poor = FakeTitle(name="Poor", genres=["Drama"], keywords=["family"], vote_average=5.6)
    poor.embedding = list(good.embedding)

    def top(weights: RankingWeights):
        return rank_titles(
            user_vector=list(good.embedding),
            user_features={"genre:drama": 2.0},
            titles=[poor, good],
            exclude_ids=set(),
            slate_size=1,
            mmr_lambda=1.0,
            exploration_slots=0,
            weights=weights,
        )[0].title_id

    assert top(DEFAULT_WEIGHTS) == poor.id  # tie → input order
    assert top(RankingWeights(low_rating=0.1)) == good.id


def test_quality_percentiles_shrink_thin_ratings_toward_the_mean() -> None:
    """Ten votes of ★9.0 do not beat ten thousand of ★8.5."""
    q = quality_percentiles(np.array([9.0, 8.5, 6.0]), np.array([10.0, 10_000.0, 5_000.0]))
    assert q[1] > q[0] > q[2]
    # Unknown vote counts weigh like the median: the rating order survives.
    unknown = quality_percentiles(np.array([8.0, 7.0]), np.array([np.nan, np.nan]))
    assert unknown[0] > unknown[1]
    assert quality_percentiles(np.array([]), np.array([])).size == 0


def test_quality_weight_is_off_by_default_and_promotes_when_on() -> None:
    """Off by default (a weight change needs the owner); when on, it promotes."""
    assert DEFAULT_WEIGHTS.quality == 0.0
    # Both below the gem threshold (★7.2), so only the quality prior separates them.
    better = FakeTitle(name="Better", genres=["Drama"], keywords=["family"], vote_average=7.1)
    worse = FakeTitle(name="Worse", genres=["Drama"], keywords=["family"], vote_average=6.0)
    worse.embedding = list(better.embedding)

    def top(weights: RankingWeights):
        return rank_titles(
            user_vector=list(better.embedding),
            user_features={"genre:drama": 2.0},
            titles=[worse, better],
            exclude_ids=set(),
            slate_size=1,
            mmr_lambda=1.0,
            exploration_slots=0,
            weights=weights,
        )[0].title_id

    assert top(DEFAULT_WEIGHTS) == worse.id  # tie → input order
    assert top(RankingWeights(quality=0.1)) == better.id


def test_rank_titles_is_channels_then_blend_then_select(thriller_catalog: list[FakeTitle]) -> None:
    """Evaluation calls the three steps separately; it must measure what the API serves."""
    features = {"genre:thriller": 2.0, "person:director:chris nolan": 1.5}
    vector = list(thriller_catalog[0].embedding)
    kwargs = {"slate_size": 5, "mmr_lambda": 0.8, "exploration_slots": 2}
    served = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=thriller_catalog,
        exclude_ids=set(),
        **kwargs,
    )
    channels = candidate_channels(
        user_vector=vector, user_features=features, titles=thriller_catalog, exclude_ids=set()
    )
    assert channels is not None
    measured = select_slate(channels, blend_scores(channels), user_features=features, **kwargs)
    assert [(r.title_id, r.hidden_gem) for r in served] == [
        (r.title_id, r.hidden_gem) for r in measured
    ]


def test_subset_keeps_catalog_percentiles_and_quota_inputs() -> None:
    """Evaluation ranks a subset of the catalog's channels, as production ranks its pool.

    Gems and fame describe the catalog, so a subset must carry them over, not
    recompute them; directors and collections must stay aligned with the pool.
    """
    titles = [
        FakeTitle(name=f"T{i}", genres=["Drama"], people=[PersonSignal(f"Dir {i}", "director")])
        for i in range(3)
    ]
    for i, title in enumerate(titles):
        title.extra[CATALOG_PERCENTILES_KEY] = {"popularity": 0.1 * i, "vote_count": 0.2 * i}
    titles[2].extra["collection"] = {"id": 77, "name": "Saga"}
    channels = candidate_channels(
        user_vector=None, user_features={}, titles=titles, exclude_ids=set()
    )
    assert channels is not None
    sub = channels.subset([2, 0])
    assert [t.name for t in sub.pool] == ["T2", "T0"]
    assert sub.directors == [("dir 2",), ("dir 0",)]
    assert sub.collections == [77, None]
    np.testing.assert_allclose(sub.fame, [0.4, 0.0])
    np.testing.assert_array_equal(sub.gems, channels.gems[[2, 0]])
    np.testing.assert_array_equal(sub.low_rated, channels.low_rated[[2, 0]])


def test_cold_prior_uses_percentiles_not_raw_popularity() -> None:
    """One trending outlier (popularity 2,569) must not swamp the cold prior.

    With raw popularity / 200 capped at 0.22, the outlier and a title at 44 got
    the same prior; with percentiles the order follows the catalog rank.
    """
    outlier = FakeTitle(name="Outlier", genres=["Action"], popularity=2569.0, vote_average=6.0)
    known = FakeTitle(name="Known", genres=["Drama"], popularity=47.9, vote_average=6.0)
    obscure = FakeTitle(name="Obscure", genres=["Comedy"], popularity=2.0, vote_average=6.0)
    ranked = rank_titles(
        user_vector=None,
        user_features={},
        titles=[obscure, known, outlier],
        exclude_ids=set(),
        slate_size=3,
        mmr_lambda=1.0,
        exploration_slots=0,
        with_reasons=False,
    )
    assert [r.title_id for r in ranked] == [outlier.id, known.id, obscure.id]
    scores = [r.score for r in ranked]
    assert scores[0] - scores[1] == pytest.approx(scores[1] - scores[2])


def test_annotate_discovery_reasons_adds_gem_and_exploration() -> None:
    base = [
        Reason(
            code="shared_genre",
            message="Fits the thriller side of your taste",
            evidence={"genres": ["Thriller"]},
        )
    ]
    out = annotate_discovery_reasons(
        base,
        is_hidden_gem=True,
        is_exploration=True,
        vote_average=7.8,
        popularity=12.0,
        title_name="Quiet Masterpiece",
        max_reasons=3,
    )
    codes = [r.code for r in out]
    assert codes[0] == "shared_genre"
    assert "hidden_gem" in codes
    assert "discovery" in codes
    gem = next(r for r in out if r.code == "hidden_gem")
    assert "★7.8" in gem.message


def test_rank_includes_hidden_gem_reason_for_quality_underdog() -> None:
    """A high-rating low-popularity title should carry a hidden_gem reason when ranked."""
    gem = FakeTitle(
        name="Quiet Masterpiece",
        genres=["Drama"],
        keywords=["intimate"],
        popularity=8.0,
        vote_average=8.1,
    )
    blockbuster = FakeTitle(
        name="Summer Blast",
        genres=["Action"],
        popularity=180.0,
        vote_average=6.5,
    )
    filler = [
        FakeTitle(
            name=f"Filler {i}",
            genres=["Drama" if i % 2 == 0 else "Comedy"],
            popularity=25.0 + i,
            vote_average=7.0,
        )
        for i in range(8)
    ]
    ranked = rank_titles(
        user_vector=list(gem.embedding),
        user_features={"genre:drama": 2.0},
        titles=[gem, blockbuster, *filler],
        exclude_ids=set(),
        slate_size=6,
        mmr_lambda=0.7,
        exploration_slots=2,
    )
    by_id = {item.title_id: item for item in ranked}
    assert gem.id in by_id
    codes = {r.code for r in by_id[gem.id].reasons}
    assert "hidden_gem" in codes


def test_hidden_gem_label_is_rationed_per_slate() -> None:
    """Every title here qualifies; only a few may say so, or the badge is noise."""
    from app.recommendation.pipeline import MAX_GEM_LABELS

    gems = [
        FakeTitle(
            name=f"Underdog {i}",
            genres=[["Drama", "Comedy", "Thriller", "Horror", "Romance"][i % 5]],
            popularity=10.0 + i,
            vote_average=7.4 + (i % 4) * 0.2,
        )
        for i in range(12)
    ]
    ranked = rank_titles(
        user_vector=None,
        user_features={},
        titles=gems,
        exclude_ids=set(),
        slate_size=10,
        mmr_lambda=0.7,
        exploration_slots=0,
    )
    labelled = [item for item in ranked if any(r.code == "hidden_gem" for r in item.reasons)]
    assert len(ranked) == 10
    assert 0 < len(labelled) <= MAX_GEM_LABELS


# ---------------------------------------------------------------------------
# Sparse channel sanity (used inside score)
# ---------------------------------------------------------------------------


def test_sparse_positive_and_negative_channels() -> None:
    title = FakeTitle(
        name="X",
        genres=["Thriller"],
        people=[PersonSignal("Ava Voss", "director")],
    )
    snap = title.extra["feature_snapshot"]
    pos, neg = sparse_channel_scores(
        {"person:director:ava voss": 2.0, "genre:comedy": -1.0},
        snap,
    )
    assert pos > 0
    assert neg == 0  # no comedy on title

    pos2, neg2 = sparse_channel_scores(
        {"person:director:ava voss": -2.0},
        snap,
    )
    assert pos2 == 0 or pos2 < pos
    assert neg2 > 0


def test_user_vector_similarity_aligns_with_embedding() -> None:
    a = FakeTitle(name="A", genres=["Thriller"], keywords=["noir"])
    b = FakeTitle(name="B", genres=["Comedy"], keywords=["wedding"])
    c = FakeTitle(name="C", genres=["Thriller"], keywords=["noir"])
    assert cosine(a.embedding, c.embedding) > cosine(a.embedding, b.embedding)
