from app.application.taste_summary import (
    humanize_feature_key,
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
