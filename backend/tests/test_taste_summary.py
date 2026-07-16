from app.application.taste_summary import (
    build_taste_export,
    format_taste_export_text,
    humanize_feature_key,
    is_allowed_feature_key,
    merge_import_overlay,
    rank_features,
    summarize_profile_features,
)
from app.recommendation.explanations import EXPLAIN_MEMORY_KEY


def test_humanize_feature_keys() -> None:
    assert humanize_feature_key("genre:science fiction") == ("Genre", "Science Fiction")
    fam, val = humanize_feature_key("person:director:christopher nolan")
    assert fam == "Director"
    assert "Nolan" in val
    assert humanize_feature_key("tone:cerebral")[0] == "Tone"
    assert humanize_feature_key("lang:en")[1] == "EN"


def test_rank_features_positive_and_negative() -> None:
    features = {
        "genre:drama": 2.1,
        "genre:comedy": 0.4,
        "person:director:ava voss": 1.8,
        "genre:horror": -1.5,
        "tone:campy": -0.6,
    }
    likes = rank_features(features, positive=True, limit=3)
    assert [c.key for c in likes] == [
        "genre:drama",
        "person:director:ava voss",
        "genre:comedy",
    ]
    assert likes[0].label == "Drama"
    assert likes[1].label.startswith("Director:")

    dislikes = rank_features(features, positive=False, limit=2)
    assert dislikes[0].key == "genre:horror"
    assert dislikes[0].weight < 0


def test_summarize_strips_explain_memory() -> None:
    raw = {
        "genre:thriller": 1.2,
        EXPLAIN_MEMORY_KEY: {
            "anchors": [
                {"name": "Inception", "weight": 1.5},
                {"name": "Heat", "weight": 1.0},
            ]
        },
    }
    out = summarize_profile_features(raw, limit=5)
    assert out["feature_count"] == 1
    assert out["anchor_count"] == 2
    assert out["likes"][0].key == "genre:thriller"
    assert all(not c.key.startswith("__") for c in out["likes"])


def test_build_taste_export_and_text() -> None:
    raw = {
        "genre:drama": 2.0,
        "genre:horror": -1.0,
        EXPLAIN_MEMORY_KEY: {
            "anchors": [{"name": "Inception", "year": 2010, "title_id": "secret"}]
        },
    }
    snap = build_taste_export(
        profile_version=4,
        updated_at=None,
        has_vector=True,
        raw_features=raw,
        exported_at="2026-07-17T12:00:00+00:00",
    )
    assert snap["schema"] == "cinetaste.taste_snapshot.v1"
    assert snap["profile_version"] == 4
    assert snap["likes"][0]["label"] == "Drama"
    assert snap["dislikes"][0]["label"] == "Horror"
    assert snap["anchors"] == [{"name": "Inception", "year": 2010}]
    text = format_taste_export_text(snap)
    assert "You lean toward" in text
    assert "Drama" in text
    assert "Horror" in text
    assert "Inception (2010)" in text
    assert "secret" not in text


def test_merge_import_overlay_scales_and_filters() -> None:
    assert is_allowed_feature_key("genre:drama")
    assert not is_allowed_feature_key("__import_overlay__")
    assert not is_allowed_feature_key("hack:me")

    overlay = merge_import_overlay(
        {"genre:drama": 1.0},
        likes=[
            {"key": "genre:drama", "weight": 2.0, "label": "Drama", "family": "genre"},
            {"key": "evil:x", "weight": 9.0, "label": "Nope", "family": "x"},
        ],
        dislikes=[
            {"key": "genre:horror", "weight": -1.0, "label": "Horror", "family": "genre"},
        ],
        scale=0.5,
    )
    assert overlay["genre:drama"] == 2.0  # 1.0 + 2.0*0.5
    assert overlay["genre:horror"] == -0.5
    assert "evil:x" not in overlay
