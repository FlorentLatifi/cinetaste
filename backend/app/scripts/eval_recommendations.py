"""Offline recommendation smoke eval (synthetic catalog).

Usage (from backend/):

    python -m app.scripts.eval_recommendations

No database required — builds a small synthetic catalog, holds out one “like”,
and reports hit-rate / precision@K.
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.recommendation.embeddings import PersonSignal, build_title_embedding, features_from_title
from app.recommendation.eval import evaluate_held_out_like


class _G:
    def __init__(self, name: str) -> None:
        self.name = name


class _T:
    def __init__(
        self,
        name: str,
        genres: list[str],
        *,
        keywords: list[str] | None = None,
        director: str | None = None,
        pop: float = 20,
        vote: float = 7.5,
    ) -> None:
        self.id = uuid4()
        self.name = name
        self.genres = [_G(g) for g in genres]
        self.popularity = pop
        self.vote_average = vote
        people = [PersonSignal(director, "director")] if director else []
        kws = keywords or genres
        self.embedding = build_title_embedding(
            name=name,
            overview=" ".join(kws),
            genres=genres,
            keywords=kws,
            people=people,
            media_type="movie",
            release_year=2018,
            runtime=110,
            popularity=pop,
            vote_average=vote,
        )
        self.extra = {
            "feature_snapshot": features_from_title(
                genres=genres,
                keywords=kws,
                people=[(p.name, p.role) for p in people],
                release_year=2018,
                runtime=110,
                media_type="movie",
            )
        }


def main() -> None:
    liked = _T(
        "Held Out Thriller",
        ["Thriller", "Crime"],
        keywords=["noir", "detective"],
        director="Ava Voss",
        pop=25,
        vote=8.1,
    )
    catalog = [
        liked,
        _T("Sister Thriller", ["Thriller"], keywords=["noir"], director="Ava Voss", pop=22),
        _T("Comedy Night", ["Comedy"], keywords=["feel-good"], director="Sam Jokes", pop=90),
        _T("Space Drama", ["Science Fiction", "Drama"], keywords=["mind-bending"], director="Chris Nolan", pop=40),
        _T("Romcom", ["Comedy", "Romance"], keywords=["wedding"], pop=70),
        _T("Horror House", ["Horror"], keywords=["suspense"], pop=35),
        _T("Quiet Drama", ["Drama"], keywords=["melancholy"], pop=15, vote=7.8),
        _T("Action Pack", ["Action"], keywords=["heist"], pop=80),
    ]

    # Profile from sister thriller features (proxy for liking Ava Voss thrillers)
    sister = catalog[1]
    user_features = dict(sister.extra["feature_snapshot"])
    # Boost director strongly as if rated favorite
    user_features["person:director:ava voss"] = user_features.get("person:director:ava voss", 0) + 2.2
    user_vector = list(sister.embedding)

    metrics = evaluate_held_out_like(
        user_vector=user_vector,
        user_features=user_features,
        catalog=catalog,
        held_out_id=liked.id,
        exclude_ids={sister.id},  # already “seen”
        slate_size=5,
        k=5,
        mmr_lambda=0.7,
    )
    print(json.dumps({"scenario": "held_out_similar_thriller", **metrics}, indent=2))


if __name__ == "__main__":
    main()
