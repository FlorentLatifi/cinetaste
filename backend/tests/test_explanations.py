"""Tests for human-readable recommendation explanations."""

from uuid import uuid4

import pytest

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


# ---------------------------------------------------------------------------
# Wording seen by real users (live audit, 2026-11)
# ---------------------------------------------------------------------------


def _reasons_for(user: dict[str, float], **title) -> list[str]:
    from app.recommendation.embeddings import features_from_title

    snap = features_from_title(
        genres=title.get("genres", ["Drama"]),
        keywords=title.get("keywords", []),
        people=title.get("people", []),
        release_year=title.get("year", 2015),
        runtime=110,
        media_type=title.get("media_type", "movie"),
        original_language=title.get("lang", "en"),
        countries=title.get("countries", ["US"]),
    )
    return [
        r.message
        for r in build_reasons(
            user_features=user,
            explain_memory={},
            title_name="Candidate",
            title_extra={"feature_snapshot": snap},
            title_genres=title.get("genres", ["Drama"]),
            similarity=0.2,
        )
    ]


@pytest.mark.parametrize(
    ("genre", "noun"),
    [("Comedy", "comedies"), ("Family", "family films"), ("Science Fiction", "sci-fi films")],
)
def test_single_genre_phrases_are_real_plurals(genre: str, noun: str) -> None:
    """Appending "s" shipped "comedys", "familys" and "war actions"."""
    from app.recommendation.explanations import _genre_phrase

    assert _genre_phrase([genre.lower()]) == noun


def test_blend_does_not_repeat_the_genre_as_a_tone() -> None:
    messages = _reasons_for(
        {"genre:war": 1.5, "genre:action": 1.0, "tone:war": 1.0},
        genres=["War", "Action"],
        keywords=["world war ii"],
    )
    blend = next(m for m in messages if m.startswith("Strong match"))
    assert "war war" not in blend.lower() and "wartime war" not in blend.lower()
    assert "actions" not in blend


def test_theme_words_match_whole_words_only() -> None:
    """A substring test made "wartime" a "time" theme and "immortality" a "moral" one."""
    messages = _reasons_for(
        {"genre:thriller": 1.5, "tone:tense": 1.0, "kw:wartime": 1.0, "kw:immortality": 1.0},
        genres=["Thriller"],
        keywords=["suspense", "wartime", "immortality"],
    )
    blend = next(m for m in messages if m.startswith("Strong match"))
    assert "exploring" not in blend


def test_no_reason_shows_a_raw_country_or_language_code() -> None:
    messages = _reasons_for(
        {"country:KR": 2.0, "lang:ko": 2.0},
        genres=["Drama"],
        lang="ko",
        countries=["KR"],
    )
    assert "Korean cinema, which you keep rating highly" in messages
    assert not any("(ko)" in m or " KR " in m for m in messages)


def test_english_and_american_origin_are_not_offered_as_reasons() -> None:
    """In a mostly-American catalog they explain nothing and take a real reason's slot."""
    messages = _reasons_for({"country:US": 2.0, "lang:en": 2.0}, genres=["Drama"])
    assert not any("cinema" in m or "language" in m for m in messages)
