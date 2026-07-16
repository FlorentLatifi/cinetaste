"""Tests for human-readable recommendation explanations."""

from uuid import uuid4

from app.recommendation.embeddings import PersonSignal, features_from_title
from app.recommendation.explanations import (
    EXPLAIN_MEMORY_KEY,
    build_anchor_from_title,
    build_reasons,
    merge_explain_memory,
    strip_explain_memory,
)
from app.recommendation.pipeline import explain


def test_strip_and_merge_explain_memory() -> None:
    features = {"genre:thriller": 1.2, "person:director:nolan": 2.0}
    anchors = [
        build_anchor_from_title(
            title_id=uuid4(),
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={"person:director:christopher nolan": 2.2, "tone:cerebral": 1.0},
            year=2010,
        )
    ]
    merged = merge_explain_memory(features, anchors)
    assert EXPLAIN_MEMORY_KEY in merged
    scoring, memory = strip_explain_memory(merged)
    assert "genre:thriller" in scoring
    assert EXPLAIN_MEMORY_KEY not in scoring
    assert len(memory["anchors"]) == 1
    assert memory["anchors"][0]["name"] == "Inception"


def test_because_you_liked_same_director() -> None:
    inception_id = uuid4()
    prestige_id = uuid4()
    anchors = [
        build_anchor_from_title(
            title_id=inception_id,
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={
                "person:director:christopher nolan": 2.2,
                "kw:mind-bending": 0.9,
                "tone:cerebral": 1.0,
                "genre:science fiction": 1.0,
            },
        ),
        build_anchor_from_title(
            title_id=prestige_id,
            name="The Prestige",
            event_type="rate_3",
            weight=1.0,
            feature_snapshot={
                "person:director:christopher nolan": 2.2,
                "kw:mind-bending": 0.9,
                "tone:cerebral": 1.0,
                "genre:drama": 1.0,
            },
        ),
    ]
    candidate = features_from_title(
        genres=["Science Fiction", "Thriller"],
        keywords=["mind-bending", "time travel"],
        people=[PersonSignal("Christopher Nolan", "director")],
        release_year=2014,
        runtime=169,
        media_type="movie",
    )
    user_features = {
        "person:director:christopher nolan": 2.5,
        "tone:cerebral": 1.5,
        "genre:science fiction": 1.2,
        "kw:mind-bending": 1.0,
    }
    reasons = build_reasons(
        user_features=user_features,
        explain_memory={"version": 1, "anchors": anchors},
        title_name="Interstellar",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Science Fiction", "Drama"],
        similarity=0.72,
    )
    assert reasons
    primary = reasons[0]
    assert primary.code == "because_you_liked"
    assert "Inception" in primary.message or "Prestige" in primary.message
    assert "director" in primary.message.lower() or "Nolan" in primary.message


def test_taste_blend_is_specific() -> None:
    candidate = features_from_title(
        genres=["Thriller"],
        keywords=["psychological thriller", "moral dilemma"],
        people=[],
        release_year=2019,
        runtime=120,
        media_type="movie",
    )
    # Force tone via keyword rules
    candidate["tone:tense"] = 1.0
    candidate["kw:psychological thriller"] = 0.9
    user_features = {
        "tone:tense": 2.0,
        "genre:thriller": 1.8,
        "kw:psychological thriller": 1.2,
    }
    reasons = build_reasons(
        user_features=user_features,
        explain_memory={"anchors": []},
        title_name="Gone Girl",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Thriller"],
        similarity=0.6,
    )
    assert any(r.code == "taste_blend" for r in reasons)
    blend = next(r for r in reasons if r.code == "taste_blend")
    assert "Strong match" in blend.message
    assert "thriller" in blend.message.lower()


def test_explain_wrapper_reads_memory_from_features() -> None:
    feats = features_from_title(
        genres=["Drama"],
        keywords=[],
        people=[PersonSignal("Nolan", "director")],
        release_year=2010,
        runtime=120,
        media_type="movie",
    )
    anchors = [
        build_anchor_from_title(
            title_id=uuid4(),
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={"person:director:nolan": 2.2},
        )
    ]
    user = merge_explain_memory(
        {"person:director:nolan": 2.0, "genre:drama": 1.0},
        anchors,
    )
    reasons = explain(
        user_features=user,
        title_extra={"feature_snapshot": feats},
        title_genres=["Drama"],
        similarity=0.7,
    )
    assert reasons
    assert all(r.message for r in reasons)
    # Should not dump mechanical "Matches genres you like" as only line when director exists
    assert reasons[0].code in {"because_you_liked", "same_director", "taste_blend", "similar_style"}
