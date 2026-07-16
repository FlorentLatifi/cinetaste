from uuid import uuid4

from app.recommendation.embeddings import PersonSignal, build_title_embedding, features_from_title
from app.recommendation.eval import evaluate_held_out_like, hit_rate_at_k, precision_at_k


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


def test_precision_and_hit_rate() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    rec = [a, b, c]
    assert precision_at_k(rec, {a}, k=2) == 0.5
    assert hit_rate_at_k(rec, {c}, k=3) == 1.0
    assert hit_rate_at_k(rec, {uuid4()}, k=2) == 0.0


def test_held_out_like_surfaces_in_slate() -> None:
    base = build_title_embedding(
        name="Target",
        overview="thriller noir rain",
        genres=["Thriller"],
        keywords=["noir"],
        people=[PersonSignal("Ava", "director")],
        media_type="movie",
        release_year=2019,
        runtime=110,
        popularity=30,
        vote_average=8.0,
    )
    held = _T(["Thriller"], base, pop=25, vote=8.0)
    comedy = _T(
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
    )
    metrics = evaluate_held_out_like(
        user_vector=base,
        user_features={"genre:thriller": 2.0, "person:director:ava": 2.0},
        catalog=[held, comedy],
        held_out_id=held.id,
        slate_size=2,
        k=1,
        mmr_lambda=1.0,
    )
    assert metrics["hit_rate_at_k"] == 1.0
    assert metrics["rank"] == 1.0
