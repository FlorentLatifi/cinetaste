"""Behavioural invariants of the recommender.

Each test pins a property the product promises (the profile reflects the user's
*current* opinion, slates are diverse, explanations only cite real evidence).
Several reproduce bugs found in the audit and would fail on the old code.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.taste_summary import merge_import_overlay, rank_features
from app.domain.taste_signals import decay_factor, effective_title_signals, weight_for
from app.recommendation.embeddings import (
    PersonSignal,
    cosine,
    features_from_title,
    tones_from_keywords,
)
from app.recommendation.explanations import build_anchor_from_title, build_reasons
from app.recommendation.pipeline import primary_genre, rank_titles
from app.recommendation.profile import ProfileTitle, build_profile
from tests.conftest import FakeTitle

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _event(title_id, event_type: str, minutes: int):
    return (title_id, event_type, weight_for(event_type), T0 + timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# One effective signal per title
# ---------------------------------------------------------------------------


def test_latest_opinion_wins_like_then_dislike_is_a_dislike() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "like", 0), _event(t, "dislike", 5)])
    assert signals[t].event_type == "dislike"
    assert signals[t].weight == pytest.approx(weight_for("dislike"))


def test_weaker_tier_does_not_override_an_opinion() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "rate_4", 0), _event(t, "watched", 5)])
    assert signals[t].event_type == "rate_4"


def test_repeated_implicit_events_count_once() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "view", i) for i in range(20)])
    assert signals[t].weight == pytest.approx(weight_for("view"))


def test_clear_removes_everything_before_it() -> None:
    t = uuid4()
    signals = effective_title_signals(
        [_event(t, "rate_4", 0), _event(t, "clear", 1)]
    )
    assert t not in signals
    signals = effective_title_signals(
        [_event(t, "rate_4", 0), _event(t, "clear", 1), _event(t, "rate_1", 2)]
    )
    assert signals[t].event_type == "rate_1"


def test_havent_seen_carries_no_signal() -> None:
    t = uuid4()
    assert effective_title_signals([_event(t, "haven't_seen", 0)]) == {}


def test_time_decay_halves_weight_after_one_half_life() -> None:
    t = uuid4()
    old = (t, "rate_3", 1.0, T0)
    signals = effective_title_signals([old], now=T0 + timedelta(days=365), half_life_days=365)
    assert signals[t].weight == pytest.approx(0.5, rel=1e-6)
    assert signals[t].raw_weight == 1.0
    assert decay_factor(10, None) == 1.0
    assert decay_factor(10, 0) == 1.0


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------


def _profile_title(fake: FakeTitle) -> ProfileTitle:
    return ProfileTitle(
        id=fake.id,
        name=fake.name,
        year=fake.release_year,
        feature_snapshot=fake.extra["feature_snapshot"],
        embedding=fake.embedding,
    )


def test_dislikes_only_profile_has_no_vector() -> None:
    hated = FakeTitle(name="Bad", genres=["Horror"], keywords=["gore"])
    signals = effective_title_signals([_event(hated.id, "rate_1", 0)])
    built = build_profile(signals.values(), {hated.id: _profile_title(hated)})
    assert built.vector is None
    assert built.features["genre:horror"] < 0


def test_vector_is_built_from_positive_signals_only() -> None:
    liked = FakeTitle(name="Liked", genres=["Science Fiction"], keywords=["space"])
    hated = FakeTitle(name="Hated", genres=["Comedy"], keywords=["wedding"])
    signals = effective_title_signals(
        [_event(liked.id, "rate_4", 0), _event(hated.id, "rate_1", 1)]
    )
    built = build_profile(
        signals.values(),
        {liked.id: _profile_title(liked), hated.id: _profile_title(hated)},
    )
    assert built.vector is not None
    assert cosine(built.vector, liked.embedding) == pytest.approx(1.0, abs=1e-6)


def test_anchors_come_from_strong_positive_opinions() -> None:
    fav = FakeTitle(name="Fav", genres=["Drama"])
    meh = FakeTitle(name="Meh", genres=["Drama"])
    signals = effective_title_signals(
        [_event(fav.id, "rate_4", 0), _event(meh.id, "rate_2", 1)]
    )
    built = build_profile(
        signals.values(), {fav.id: _profile_title(fav), meh.id: _profile_title(meh)}
    )
    assert [a["name"] for a in built.anchors] == ["Fav"]


# ---------------------------------------------------------------------------
# Ranking: diversity, exploration, cold start
# ---------------------------------------------------------------------------


def _drama_heavy_catalog() -> list[FakeTitle]:
    dramas = [
        FakeTitle(
            name=f"Drama {i}",
            genres=["Drama"],
            keywords=["family"],
            people=[PersonSignal(f"Dir{i % 7}", "director")],
            vote_average=6.0 + (i % 7) * 0.1,
            popularity=5.0 + i,
        )
        for i in range(60)
    ]
    comedies = [
        FakeTitle(name=f"Comedy {i}", genres=["Comedy"], keywords=["satire"], vote_average=6.0)
        for i in range(10)
    ]
    return dramas + comedies


def _profile_from(titles: list[FakeTitle]) -> tuple[list[float], dict[str, float]]:
    signals = effective_title_signals([_event(t.id, "rate_4", i) for i, t in enumerate(titles)])
    built = build_profile(signals.values(), {t.id: _profile_title(t) for t in titles})
    assert built.vector is not None
    return built.vector, built.features


def test_genre_cap_survives_backfill() -> None:
    """Audit repro: the old backfill loop produced a 20/20 Drama slate."""
    catalog = _drama_heavy_catalog()
    liked = catalog[:5]
    vector, features = _profile_from(liked)
    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in liked},
        slate_size=20,
        mmr_lambda=0.7,
        exploration_slots=3,
    )
    by_id = {t.id: t for t in catalog}
    counts = Counter(primary_genre(by_id[r.title_id]) for r in ranked)
    assert len(ranked) == 20
    # Only two genres exist, so the cap is raised evenly: comedies are not
    # crowded out by higher-scoring dramas.
    assert counts == {"drama": 10, "comedy": 10}


def test_cap_relaxes_only_when_catalog_has_one_genre() -> None:
    catalog = [FakeTitle(name=f"D{i}", genres=["Drama"]) for i in range(30)]
    ranked = rank_titles(
        user_vector=list(catalog[0].embedding),
        user_features={"genre:drama": 2.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=10,
        mmr_lambda=0.7,
        exploration_slots=2,
    )
    assert len(ranked) == 10


def _mixed_catalog() -> list[FakeTitle]:
    genres = ["Drama", "Comedy", "Thriller", "Horror", "Documentary", "Animation"]
    return [
        FakeTitle(
            name=f"{genres[i % len(genres)]} {i}",
            genres=[genres[i % len(genres)]],
            keywords=[f"kw{i % 9}"],
            vote_average=7.0 + (i % 5) * 0.2,
            popularity=10.0 + i,
        )
        for i in range(120)
    ]


def test_exploration_respects_slot_count_and_is_labelled() -> None:
    catalog = _mixed_catalog()
    drama_fans = [t for t in catalog if t.name.startswith("Drama")][:4]
    vector, features = _profile_from(drama_fans)
    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in drama_fans},
        slate_size=20,
        mmr_lambda=0.7,
        exploration_slots=3,
    )
    exploration = [
        i for i, r in enumerate(ranked) if any(reason.code == "discovery" for reason in r.reasons)
    ]
    assert 1 <= len(exploration) <= 3
    assert 0 not in exploration  # the first card is always the best match


def test_dislike_heavy_user_is_treated_as_cold_start() -> None:
    catalog = [
        FakeTitle(name="Blockbuster", genres=["Action"], popularity=300.0, vote_average=6.5),
        FakeTitle(name="Niche", genres=["Documentary"], popularity=2.0, vote_average=6.5),
        FakeTitle(name="Scary", genres=["Horror"], popularity=250.0, vote_average=6.5),
    ]
    ranked = rank_titles(
        user_vector=None,
        user_features={"genre:horror": -3.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=3,
        mmr_lambda=1.0,
        exploration_slots=0,
    )
    assert ranked[0].title_id == catalog[0].id


# ---------------------------------------------------------------------------
# Features and explanations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyword", ["dwarf", "award ceremony", "warehouse", "intense rivalry"])
def test_tone_rules_match_whole_words_only(keyword: str) -> None:
    assert tones_from_keywords([keyword]) == {}


def test_tone_rules_still_match_real_phrases() -> None:
    assert "war" in tones_from_keywords(["world war ii"])
    assert "horror" in tones_from_keywords(["body horror"])


def test_keyword_features_are_labelled_as_themes() -> None:
    chips = rank_features({"kw:heist": 1.2}, positive=True)
    assert chips[0].family == "kw"
    assert chips[0].label.startswith("Theme")


def test_import_maps_legacy_keyword_prefix() -> None:
    overlay = merge_import_overlay(
        None, likes=[{"key": "keyword:heist", "weight": 1.0}], dislikes=[]
    )
    assert "kw:heist" in overlay


def _anchor(name: str, event_type: str) -> dict:
    return build_anchor_from_title(
        title_id=uuid4(),
        name=name,
        event_type=event_type,
        weight=weight_for(event_type),
        feature_snapshot={"person:director:ava voss": 2.2, "genre:thriller": 1.0},
    )


def _reasons_for(anchor: dict) -> list:
    candidate = features_from_title(
        genres=["Thriller"],
        keywords=[],
        people=[PersonSignal("Ava Voss", "director")],
        release_year=2020,
        runtime=110,
        media_type="movie",
    )
    return build_reasons(
        user_features={"person:director:ava voss": 2.0, "genre:thriller": 1.0},
        explain_memory={"anchors": [anchor]},
        title_name="Candidate",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Thriller"],
        similarity=0.5,
    )


def test_explanation_says_rated_only_for_ratings() -> None:
    rated = _reasons_for(_anchor("Rated One", "rate_4"))[0].message
    liked = _reasons_for(_anchor("Liked One", "like"))[0].message
    assert "rated Rated One highly" in rated
    assert "liked Liked One" in liked
    assert "rated" not in liked


def test_explanations_do_not_claim_unmodelled_pacing() -> None:
    candidate = features_from_title(
        genres=["Comedy"],
        keywords=["satire"],
        people=[],
        release_year=2020,
        runtime=100,
        media_type="movie",
    )
    reasons = build_reasons(
        user_features={"tone:satirical": 2.0, "genre:comedy": 1.5, "kw:satire": 1.0},
        explain_memory={"anchors": []},
        title_name="Satire",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Comedy"],
        similarity=0.4,
    )
    assert reasons
    assert not any("pacing" in r.message for r in reasons)
