from uuid import uuid4

from app.recommendation.embeddings import build_title_embedding, cosine, features_from_title
from app.recommendation.pipeline import explain, mmr_select, rank_titles


def test_embeddings_are_normalized_and_sensitive_to_genre() -> None:
    a = build_title_embedding(
        name="Dark City",
        overview="A noir detective story",
        genres=["Thriller", "Crime"],
        keywords=["detective"],
        people=["Jane Doe"],
        media_type="movie",
        release_year=2019,
        runtime=110,
        popularity=40,
        vote_average=7.5,
    )
    b = build_title_embedding(
        name="Laugh Track",
        overview="A bright romantic comedy",
        genres=["Comedy", "Romance"],
        keywords=["wedding"],
        people=["John Smith"],
        media_type="movie",
        release_year=2019,
        runtime=100,
        popularity=40,
        vote_average=7.0,
    )
    c = build_title_embedding(
        name="Harbor Crime",
        overview="Port city thriller",
        genres=["Thriller", "Crime"],
        keywords=["detective"],
        people=["Jane Doe"],
        media_type="movie",
        release_year=2018,
        runtime=115,
        popularity=30,
        vote_average=7.2,
    )
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert cosine(a, c) > cosine(a, b)


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


def test_explain_returns_human_reasons() -> None:
    user_features = {"genre:thriller": 2.0, "person:director:nolan": 1.5}
    title_features = features_from_title(
        genres=["Thriller"],
        keywords=["time"],
        people=[("Nolan", "director")],
        release_year=2010,
        runtime=140,
        media_type="movie",
    )
    reasons = explain(
        user_features=user_features,
        title_extra={"feature_snapshot": title_features},
        title_genres=["Thriller"],
        similarity=0.7,
    )
    assert reasons
    assert all(r.message for r in reasons)


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
        people=["A"],
        media_type="movie",
        release_year=2019,
        runtime=110,
        popularity=30,
        vote_average=8.0,
    )
    titles = [
        _T(["Thriller", "Crime"], base, pop=25, vote=8.0),
        _T(["Comedy"], build_title_embedding(
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
        ), pop=90, vote=6.5),
        _T(["Thriller"], build_title_embedding(
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
        ), pop=20, vote=7.4),
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
