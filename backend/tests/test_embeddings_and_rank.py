from uuid import uuid4

from app.recommendation.embeddings import (
    PersonSignal,
    build_title_embedding,
    cosine,
    features_from_title,
    normalize_feature_families,
    sparse_channel_scores,
    tones_from_keywords,
)
from app.recommendation.pipeline import explain, mmr_select, rank_titles


def test_embeddings_are_normalized_and_sensitive_to_genre() -> None:
    a = build_title_embedding(
        name="Dark City",
        overview="A noir detective story",
        genres=["Thriller", "Crime"],
        keywords=["detective", "neo-noir"],
        people=[PersonSignal("Jane Doe", "cast", 0)],
        media_type="movie",
        release_year=2019,
        runtime=110,
        popularity=40,
        vote_average=7.5,
        original_language="en",
        countries=["US"],
    )
    b = build_title_embedding(
        name="Laugh Track",
        overview="A bright romantic comedy",
        genres=["Comedy", "Romance"],
        keywords=["wedding", "feel-good"],
        people=[PersonSignal("John Smith", "cast", 0)],
        media_type="movie",
        release_year=2019,
        runtime=100,
        popularity=40,
        vote_average=7.0,
        original_language="en",
        countries=["US"],
    )
    c = build_title_embedding(
        name="Harbor Crime",
        overview="Port city thriller",
        genres=["Thriller", "Crime"],
        keywords=["detective", "neo-noir"],
        people=[PersonSignal("Jane Doe", "cast", 0)],
        media_type="movie",
        release_year=2018,
        runtime=115,
        popularity=30,
        vote_average=7.2,
        original_language="en",
        countries=["US"],
    )
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert cosine(a, c) > cosine(a, b)


def test_cast_billing_and_director_weights() -> None:
    feats = features_from_title(
        genres=["Drama"],
        keywords=["time travel"],
        people=[
            PersonSignal("Chris Nolan", "director"),
            PersonSignal("Lead Actor", "cast", 0),
            PersonSignal("Bit Part", "cast", 7),
        ],
        release_year=2010,
        runtime=140,
        media_type="movie",
        original_language="en",
        countries=["GB", "US"],
    )
    assert feats["person:director:chris nolan"] == 2.2
    assert feats["person:cast:lead actor"] == 1.5
    assert feats["person:cast:bit part"] == 0.4
    assert feats["lang:en"] == 0.85
    assert feats["country:GB"] == 0.8
    assert feats["country:US"] == 0.8
    assert feats.get("tone:cerebral", 0) > 0
    assert feats["genre:drama"] == 1.0


def test_tones_from_keywords() -> None:
    tones = tones_from_keywords(["neo-noir", "feel-good", "time travel"])
    assert "dark" in tones
    assert "uplifting" in tones
    assert "cerebral" in tones


def test_family_normalization_caps_keyword_flood() -> None:
    flooded = {f"kw:tag{i}": 2.0 for i in range(30)}
    flooded["person:director:nolan"] = 2.0
    normed = normalize_feature_families(flooded)
    kw_l2 = sum(v * v for k, v in normed.items() if k.startswith("kw:")) ** 0.5
    dir_l2 = sum(v * v for k, v in normed.items() if k.startswith("person:director:")) ** 0.5
    assert kw_l2 <= 2.55
    assert dir_l2 >= 1.9


def test_sparse_channel_rewards_director_overlap() -> None:
    user = {
        "person:director:nolan": 2.0,
        "genre:thriller": 1.0,
    }
    title_dir = features_from_title(
        genres=["Thriller"],
        keywords=[],
        people=[PersonSignal("Nolan", "director")],
        release_year=2010,
        runtime=120,
        media_type="movie",
    )
    title_genre_only = features_from_title(
        genres=["Thriller"],
        keywords=[],
        people=[],
        release_year=2010,
        runtime=120,
        media_type="movie",
    )
    pos_dir, _ = sparse_channel_scores(user, title_dir)
    pos_genre, _ = sparse_channel_scores(user, title_genre_only)
    assert pos_dir > pos_genre


def test_negative_sparse_penalty() -> None:
    user = {"person:director:bay": -2.0, "genre:action": 1.0}
    title = features_from_title(
        genres=["Action"],
        keywords=[],
        people=[PersonSignal("Bay", "director")],
        release_year=2015,
        runtime=130,
        media_type="movie",
    )
    pos, neg = sparse_channel_scores(user, title)
    assert neg > 0
    assert pos > 0  # genre still positive


def test_mmr_prefers_diversity() -> None:
    v1 = [1.0] + [0.0] * 383
    v2 = [0.99] + [0.01] + [0.0] * 382
    v3 = [0.0, 1.0] + [0.0] * 382
    ids = [uuid4(), uuid4(), uuid4()]
    selected = mmr_select(
        [(ids[0], 1.0, v1), (ids[1], 0.95, v2), (ids[2], 0.7, v3)],
        k=2,
        lambda_mult=0.5,
    )
    assert ids[0] in selected
    assert ids[2] in selected


def test_explain_prefers_director_over_genre() -> None:
    user_features = {
        "genre:thriller": 2.0,
        "person:director:nolan": 2.5,
        "tone:cerebral": 1.2,
    }
    title_features = features_from_title(
        genres=["Thriller"],
        keywords=["time travel", "mind-bending"],
        people=[PersonSignal("Nolan", "director")],
        release_year=2010,
        runtime=140,
        media_type="movie",
        original_language="en",
    )
    reasons = explain(
        user_features=user_features,
        title_extra={"feature_snapshot": title_features},
        title_genres=["Thriller"],
        similarity=0.7,
        explain_memory={
            "anchors": [
                {
                    "title_id": "x",
                    "name": "Inception",
                    "weight": 1.55,
                    "directors": ["nolan"],
                    "cast": [],
                    "writers": [],
                    "tones": ["cerebral"],
                    "keywords": ["mind-bending"],
                    "genres": ["thriller"],
                }
            ]
        },
    )
    assert reasons
    assert all(r.message for r in reasons)
    assert reasons[0].code in {"because_you_liked", "same_director"}
    assert "Inception" in reasons[0].message or "Nolan" in reasons[0].message


class _G:
    def __init__(self, name: str) -> None:
        self.name = name


class _T:
    def __init__(self, genres: list[str], emb: list[float], pop: float = 20, vote: float = 7.5):
        self.id = uuid4()
        self.genres = [_G(g) for g in genres]
        self.embedding = emb
        self.extra = {
            "feature_snapshot": features_from_title(
                genres=genres,
                keywords=genres,
                people=[],
                release_year=2020,
                runtime=110,
                media_type="movie",
            )
        }
        self.popularity = pop
        self.vote_average = vote


def test_rank_titles_returns_slate() -> None:
    base = build_title_embedding(
        name="UserLike",
        overview="thriller crime rain",
        genres=["Thriller", "Crime"],
        keywords=["noir"],
        people=[PersonSignal("A", "cast", 0)],
        media_type="movie",
        release_year=2019,
        runtime=110,
        popularity=30,
        vote_average=8.0,
    )
    titles = [
        _T(["Thriller", "Crime"], base, pop=25, vote=8.0),
        _T(
            ["Comedy"],
            build_title_embedding(
                name="Funny",
                overview="jokes",
                genres=["Comedy"],
                keywords=[],
                people=[],
                media_type="movie",
                release_year=2019,
                runtime=100,
                popularity=90,
                vote_average=6.5,
            ),
            pop=90,
            vote=6.5,
        ),
        _T(
            ["Thriller"],
            build_title_embedding(
                name="Another Thriller",
                overview="suspense",
                genres=["Thriller"],
                keywords=["noir"],
                people=[],
                media_type="movie",
                release_year=2018,
                runtime=105,
                popularity=20,
                vote_average=7.4,
            ),
            pop=20,
            vote=7.4,
        ),
    ]
    ranked = rank_titles(
        user_vector=base,
        user_features={"genre:thriller": 2.0, "genre:crime": 1.5},
        titles=titles,
        exclude_ids=set(),
        slate_size=2,
        mmr_lambda=0.7,
        exploration_slots=0,
    )
    assert len(ranked) == 2
    assert ranked[0].reasons
