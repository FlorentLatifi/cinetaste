"""Datasets for offline evaluation.

* ``build_synthetic_dataset`` — deterministic, no download. Users are generated
  with latent preferences over the *same* kinds of features the recommender
  scores (genre, director, keywords), so absolute numbers are optimistic by
  construction. Use it to compare strategies and catch regressions, not as
  evidence of real-world accuracy.
* ``load_movielens_dataset`` — real ratings from real people (MovieLens
  ml-latest-small). Content features come from MovieLens genres, user tags and
  the release year. There is no cast or director data, so the people signals
  the production catalog relies on are absent; treat the numbers as a floor.
"""

from __future__ import annotations

import csv
import random
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.recommendation.embeddings import build_title_signals
from app.recommendation.evaluation import Dataset, EvalTitle, EvalUser

T0 = datetime(2024, 1, 1, tzinfo=UTC)

# --------------------------------------------------------------------- synthetic

_CLUSTERS = [
    ("Thriller", "Crime", ["neo-noir", "detective", "heist"], (1990, 2024)),
    ("Science Fiction", "Adventure", ["space", "time travel", "dystopia"], (1977, 2024)),
    ("Comedy", "Romance", ["feel-good", "wedding", "friendship"], (1995, 2024)),
    ("Drama", "History", ["war", "family saga", "coming of age"], (1960, 2024)),
    ("Horror", "Mystery", ["haunted house", "found footage", "slasher"], (1980, 2024)),
    ("Animation", "Family", ["talking animals", "fairy tale", "school"], (1990, 2024)),
    ("Action", "Thriller", ["martial arts", "spy", "car chase"], (1985, 2024)),
    ("Documentary", "History", ["nature", "biography", "investigation"], (2000, 2024)),
]


def build_synthetic_dataset(
    *,
    n_titles: int = 800,
    n_users: int = 200,
    ratings_per_user: int = 24,
    held_out: int = 3,
    seed: int = 7,
) -> Dataset:
    rng = random.Random(seed)
    titles: list[EvalTitle] = []
    by_cluster: dict[int, list[EvalTitle]] = defaultdict(list)

    for index in range(n_titles):
        cluster = index % len(_CLUSTERS)
        primary, secondary, keyword_pool, (year_from, year_to) = _CLUSTERS[cluster]
        director = f"Director {cluster}-{rng.randint(0, 11)}"
        cast = [f"Actor {cluster}-{rng.randint(0, 60)}" for _ in range(4)]
        keywords = rng.sample(keyword_pool, 2) + [f"topic-{rng.randint(0, 80)}"]
        year = rng.randint(year_from, year_to)
        embedding, _features, meta = build_title_signals(
            name=f"{primary} Story {index}",
            overview=f"A {primary.lower()} about {keywords[0]} and {keywords[1]}.",
            genres=[primary, secondary],
            keywords=keywords,
            people=[(director, "director"), *[(name, "cast") for name in cast]],
            media_type="movie",
            release_year=year,
            runtime=rng.randint(85, 150),
            original_language="en",
            countries=["US"],
        )
        title = EvalTitle(
            id=uuid4(),
            name=f"{primary} Story {index}",
            embedding=embedding,
            extra=meta,
            popularity=round(rng.lognormvariate(2.5, 1.0), 2),
            vote_average=round(rng.uniform(5.0, 8.8), 1),
            vote_count=rng.randint(60, 4000),
            year=year,
            genre_names=(primary, secondary),
        )
        titles.append(title)
        by_cluster[cluster].append(title)

    users: list[EvalUser] = []
    for user_index in range(n_users):
        loved, also_liked = rng.sample(range(len(_CLUSTERS)), 2)
        disliked = rng.choice([c for c in range(len(_CLUSTERS)) if c not in {loved, also_liked}])

        positives = rng.sample(by_cluster[loved], min(len(by_cluster[loved]), ratings_per_user // 2))
        positives += rng.sample(
            by_cluster[also_liked], min(len(by_cluster[also_liked]), ratings_per_user // 4)
        )
        negatives = rng.sample(
            by_cluster[disliked], min(len(by_cluster[disliked]), ratings_per_user // 4)
        )
        rng.shuffle(positives)

        test = {title.id for title in positives[:held_out]}
        events: list[tuple[UUID, str, float, datetime]] = []
        for offset, title in enumerate(positives[held_out:]):
            event = "rate_4" if rng.random() < 0.6 else "rate_3"
            events.append((title.id, event, 1.55 if event == "rate_4" else 1.0, T0 + timedelta(minutes=offset)))
        for offset, title in enumerate(negatives, start=len(events)):
            events.append((title.id, "rate_1", -0.90, T0 + timedelta(minutes=offset)))

        if events and test:
            users.append(EvalUser(user_id=f"synthetic-{user_index}", train_events=events, test_positive_ids=test))

    return Dataset(name="synthetic", titles=titles, users=users)


# -------------------------------------------------------------------- MovieLens

_YEAR_RE = re.compile(r"\((\d{4})\)\s*$")
# MovieLens stars → CineTaste event. 3 stars is "it was ok" (a rating, not a
# positive one), matching the product's own scale.
_EVENT_BY_RATING = {
    5.0: ("rate_4", 1.55),
    4.5: ("rate_4", 1.55),
    4.0: ("rate_3", 1.00),
    3.5: ("rate_2", 0.30),
    3.0: ("mid", 0.10),
    2.5: ("not_interested", -0.40),
    2.0: ("rate_1", -0.90),
    1.5: ("rate_1", -0.90),
    1.0: ("rate_1", -0.90),
    0.5: ("rate_1", -0.90),
}
POSITIVE_RATING_THRESHOLD = 4.0


def load_movielens_dataset(
    directory: str | Path,
    *,
    min_ratings: int = 20,
    max_users: int | None = 500,
    test_fraction: float = 0.2,
    seed: int = 7,
) -> Dataset:
    """Load ml-latest-small (movies.csv, ratings.csv, optional tags.csv).

    Split is temporal per user: the most recent ``test_fraction`` of their
    ratings are held out, and the held-out titles rated >= 4 stars are the
    relevant set. Recommending something a user later rated highly is the
    closest offline proxy for "a good recommendation".
    """
    directory = Path(directory)
    movies_path = directory / "movies.csv"
    ratings_path = directory / "ratings.csv"
    if not movies_path.exists() or not ratings_path.exists():
        raise FileNotFoundError(
            f"MovieLens files not found in {directory}. Download ml-latest-small from "
            "https://grouplens.org/datasets/movielens/ and unzip it there."
        )

    tags: dict[str, list[str]] = defaultdict(list)
    tags_path = directory / "tags.csv"
    if tags_path.exists():
        with tags_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                tag = row["tag"].strip().lower()
                if tag and len(tags[row["movieId"]]) < 12:
                    tags[row["movieId"]].append(tag)

    rating_count: dict[str, int] = defaultdict(int)
    rating_sum: dict[str, float] = defaultdict(float)
    with ratings_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rating_count[row["movieId"]] += 1
            rating_sum[row["movieId"]] += float(row["rating"])

    titles: list[EvalTitle] = []
    id_by_movie: dict[str, UUID] = {}
    with movies_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = row["movieId"]
            if rating_count[movie_id] == 0:
                continue
            raw_title = row["title"].strip()
            match = _YEAR_RE.search(raw_title)
            year = int(match.group(1)) if match else None
            name = _YEAR_RE.sub("", raw_title).strip()
            genres = [g for g in row["genres"].split("|") if g and g != "(no genres listed)"]
            keywords = tags.get(movie_id, [])
            embedding, _features, meta = build_title_signals(
                name=name,
                overview=None,  # MovieLens ships no synopses
                genres=genres,
                keywords=keywords,
                people=[],  # nor cast/crew
                media_type="movie",
                release_year=year,
                runtime=None,
            )
            title_id = uuid4()
            id_by_movie[movie_id] = title_id
            titles.append(
                EvalTitle(
                    id=title_id,
                    name=name,
                    embedding=embedding,
                    extra=meta,
                    popularity=float(rating_count[movie_id]),
                    vote_average=round(rating_sum[movie_id] / rating_count[movie_id] * 2, 2),
                    vote_count=rating_count[movie_id],
                    year=year,
                    genre_names=tuple(genres),
                )
            )

    by_user: dict[str, list[tuple[float, str, float]]] = defaultdict(list)
    with ratings_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["movieId"] in id_by_movie:
                by_user[row["userId"]].append(
                    (float(row["timestamp"]), row["movieId"], float(row["rating"]))
                )

    rng = random.Random(seed)
    user_ids = [uid for uid, rows in by_user.items() if len(rows) >= min_ratings]
    user_ids.sort()
    if max_users is not None and len(user_ids) > max_users:
        user_ids = rng.sample(user_ids, max_users)

    users: list[EvalUser] = []
    for user_id in user_ids:
        rows = sorted(by_user[user_id])
        split_at = max(int(len(rows) * (1 - test_fraction)), 1)
        train_rows, test_rows = rows[:split_at], rows[split_at:]

        events: list[tuple[UUID, str, float, datetime]] = []
        for timestamp, movie_id, stars in train_rows:
            event_type, weight = _EVENT_BY_RATING[stars]
            events.append(
                (
                    id_by_movie[movie_id],
                    event_type,
                    weight,
                    datetime.fromtimestamp(timestamp, tz=UTC),
                )
            )
        test_ids = {
            id_by_movie[movie_id]
            for _ts, movie_id, stars in test_rows
            if stars >= POSITIVE_RATING_THRESHOLD
        }
        gains = {
            id_by_movie[movie_id]: (2.0 if stars >= 4.5 else 1.0)
            for _ts, movie_id, stars in test_rows
            if stars >= POSITIVE_RATING_THRESHOLD
        }
        if events and test_ids:
            users.append(
                EvalUser(
                    user_id=f"ml-{user_id}",
                    train_events=events,
                    test_positive_ids=test_ids,
                    test_gains=gains,
                )
            )

    return Dataset(name="movielens", titles=titles, users=users)
