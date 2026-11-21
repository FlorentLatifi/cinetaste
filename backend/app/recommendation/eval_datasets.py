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
from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY, percent_rank

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def stamp_catalog_percentiles(catalog: list[EvalTitle]) -> list[EvalTitle]:
    """Store catalog-wide percentiles in ``extra`` the way ingest does.

    Mirrors ``app/application/catalog_percentiles.py``: SQL ``percent_rank()``
    over every title in the catalog, rounded to 4 places. Without it, ranking
    would fall back to percentiles of whatever pool it is handed, which in
    evaluation is the catalog minus the user's own ratings.
    """
    popularity = percent_rank([t.popularity for t in catalog])
    votes = percent_rank([float(t.vote_count) for t in catalog])
    for i, title in enumerate(catalog):
        title.extra = {
            **(title.extra or {}),
            CATALOG_PERCENTILES_KEY: {
                "popularity": round(float(popularity[i]), 4),
                "vote_count": round(float(votes[i]), 4),
            },
        }
    return catalog

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

    return Dataset(name="synthetic", titles=stamp_catalog_percentiles(titles), users=users)


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

    return Dataset(name="movielens", titles=stamp_catalog_percentiles(titles), users=users)


# ------------------------------------------------------- MovieLens + TMDb

# Held-out ratings at or below this many stars are "dislikes" (CineTaste rate_1).
DISLIKE_RATING_THRESHOLD = 2.0
# The production catalog after a default ingest (docs/DEPLOY.md: ~4–4.7k titles).
PRODUCTION_CATALOG_SIZE = 4500


def load_movielens_tmdb_dataset(
    directory: str | Path,
    cache_path: str | Path,
    *,
    catalog_size: int | None = PRODUCTION_CATALOG_SIZE,
    min_ratings: int = 20,
    max_users: int | None = None,
    test_fraction: float = 0.2,
    seed: int = 7,
) -> Dataset:
    """MovieLens ratings joined (links.csv → tmdbId) to cached TMDb metadata.

    Title features come from ``signal_inputs_from_payload`` →
    ``build_title_signals`` — the ingest path — so directors, writers, cast,
    keywords, tones, synopsis words, language and countries are all present.
    Ranking priors use TMDb's ``popularity`` / ``vote_average`` /
    ``vote_count``, as production does.

    * **Split** — temporal per user: the most recent ``test_fraction`` of each
      user's ratings is held out. Held-out ≥ 4 stars are relevant; held-out
      ≤ ``DISLIKE_RATING_THRESHOLD`` stars are dislikes.
    * **Catalog** — the ``catalog_size`` titles with the most TMDb votes (the
      closest stand-in for ingest's popularity / vote-count discovery), so
      coverage and candidate retrieval behave at production scale. Titles
      outside it still shape profiles when users rated them, but are never
      candidates or held-out targets.
    * **No leakage into the baseline** — ``train_interactions`` counts only
      training-split ratings, so the in-domain popularity baseline cannot see
      the held-out period.

    Ratings of films TMDb has no details for are dropped (``notes`` says how many).
    """
    from app.application.catalog_ingest import tmdb_collection
    from app.recommendation.pipeline import COLLECTION_KEY
    from app.recommendation.tmdb_eval_cache import (
        STATUS_OK,
        load_cache,
        read_movielens_links,
        signal_inputs_from_payload,
    )

    directory = Path(directory)
    links = read_movielens_links(directory)
    cache = load_cache(Path(cache_path))
    if not cache:
        raise FileNotFoundError(
            f"No TMDb cache at {cache_path}. Run python -m app.scripts.fetch_eval_metadata first."
        )

    # One title per TMDb id (a few MovieLens ids point at the same film).
    title_by_tmdb: dict[int, EvalTitle] = {}
    title_by_movie: dict[str, EvalTitle] = {}
    for movie_id, tmdb_id in links.items():
        record = cache.get(tmdb_id)
        if record is None or record.get("status") != STATUS_OK:
            continue
        title = title_by_tmdb.get(tmdb_id)
        if title is None:
            payload = record["payload"]
            inputs = signal_inputs_from_payload("movie", payload)
            embedding, _features, meta = build_title_signals(**inputs)
            # The franchise, as ingest stores it. Caches fetched before the
            # field was kept have no collections: the cap is then idle.
            collection = tmdb_collection(payload.get("belongs_to_collection"))
            if collection:
                meta = {**meta, COLLECTION_KEY: collection}
            title = EvalTitle(
                id=uuid4(),
                name=inputs["name"],
                embedding=embedding,
                extra=meta,
                popularity=float(payload.get("popularity") or 0.0),
                vote_average=float(payload.get("vote_average") or 0.0),
                vote_count=int(payload.get("vote_count") or 0),
                year=inputs["release_year"],
                genre_names=tuple(inputs["genres"]),
            )
            title_by_tmdb[tmdb_id] = title
        title_by_movie[movie_id] = title

    ratings_total = 0
    by_user: dict[str, list[tuple[float, str, float]]] = defaultdict(list)
    with (directory / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ratings_total += 1
            if row["movieId"] in title_by_movie:
                by_user[row["userId"]].append(
                    (float(row["timestamp"]), row["movieId"], float(row["rating"]))
                )
    ratings_kept = sum(len(rows) for rows in by_user.values())

    # Split everyone first: the popularity baseline counts training ratings only.
    splits: dict[str, tuple[list[tuple[float, str, float]], list[tuple[float, str, float]]]] = {}
    for user_id, rows in by_user.items():
        rows.sort()
        if len(rows) < min_ratings:
            for _ts, movie_id, _stars in rows:
                title_by_movie[movie_id].train_interactions += 1
            continue
        split_at = max(int(len(rows) * (1 - test_fraction)), 1)
        splits[user_id] = (rows[:split_at], rows[split_at:])
        for _ts, movie_id, _stars in rows[:split_at]:
            title_by_movie[movie_id].train_interactions += 1

    every_title = list(title_by_tmdb.values())
    ranked = sorted(every_title, key=lambda t: (t.vote_count, t.popularity), reverse=True)
    if catalog_size is not None and catalog_size < len(ranked):
        catalog, profile_only = ranked[:catalog_size], ranked[catalog_size:]
    else:
        catalog, profile_only = ranked, []
    in_catalog = {t.id for t in catalog}

    rng = random.Random(seed)
    user_ids = sorted(splits)
    if max_users is not None and len(user_ids) > max_users:
        user_ids = rng.sample(user_ids, max_users)

    users: list[EvalUser] = []
    for user_id in user_ids:
        train_rows, test_rows = splits[user_id]
        events: list[tuple[UUID, str, float, datetime]] = []
        for timestamp, movie_id, stars in train_rows:
            event_type, weight = _EVENT_BY_RATING[stars]
            events.append(
                (
                    title_by_movie[movie_id].id,
                    event_type,
                    weight,
                    datetime.fromtimestamp(timestamp, tz=UTC),
                )
            )
        seen = {event[0] for event in events}

        gains: dict[UUID, float] = {}
        dislikes: set[UUID] = set()
        for _ts, movie_id, stars in test_rows:
            tid = title_by_movie[movie_id].id
            if tid not in in_catalog or tid in seen:
                continue
            if stars >= POSITIVE_RATING_THRESHOLD:
                gains[tid] = 2.0 if stars >= 4.5 else 1.0
            elif stars <= DISLIKE_RATING_THRESHOLD:
                dislikes.add(tid)
        if events and gains:
            users.append(
                EvalUser(
                    user_id=f"ml-{user_id}",
                    train_events=events,
                    test_positive_ids=set(gains),
                    test_gains=gains,
                    test_negative_ids=dislikes,
                )
            )

    return Dataset(
        name="movielens-tmdb",
        titles=stamp_catalog_percentiles(catalog),
        users=users,
        profile_only_titles=profile_only,
        notes={
            "movielens_movies": len(links),
            "titles_with_tmdb_metadata": len(every_title),
            "catalog_titles": len(catalog),
            "ratings_total": ratings_total,
            "ratings_with_metadata": ratings_kept,
            "users_evaluated": len(users),
            "users_with_heldout_dislikes": sum(1 for u in users if u.test_negative_ids),
            "heldout_likes": sum(len(u.test_positive_ids) for u in users),
            "heldout_dislikes": sum(len(u.test_negative_ids) for u in users),
        },
    )
