"""MovieLens + TMDb evaluation: loader rules and the beyond-accuracy metrics."""

from __future__ import annotations

import json
import math
import zlib
from pathlib import Path
from typing import Any

import pytest

from app.recommendation import metrics as M
from app.recommendation.eval_datasets import build_synthetic_dataset, load_movielens_tmdb_dataset
from app.recommendation.evaluation import (
    DEFAULT_STRATEGIES,
    PROD_POPULAR_CANDIDATES,
    _context,
    make_ranking_strategy,
    paired_deltas,
    paired_deltas_against,
    production_variant,
    rating_count_strategy,
    run_evaluation,
)
from app.recommendation.pipeline import CATALOG_PERCENTILES_KEY, COLLECTION_KEY, MAX_GEM_LABELS


def _payload(tmdb_id: int, *, director: str, votes: int, genre: str) -> dict[str, Any]:
    # Ann Director's two films are one franchise.
    collection = {"id": 900, "name": "Ann Saga"} if director == "Ann Director" else None
    return {
        "belongs_to_collection": collection,
        "id": tmdb_id,
        "title": f"Film {tmdb_id}",
        "overview": f"A {genre.lower()} story number {tmdb_id}.",
        "release_date": "2001-05-01",
        "runtime": 100,
        "original_language": "en",
        "popularity": float(votes) / 100,
        "vote_average": 7.0,
        "vote_count": votes,
        "genres": [{"id": 1, "name": genre}],
        "keywords": {"keywords": [{"id": tmdb_id, "name": f"topic {tmdb_id % 3}"}]},
        "production_countries": [{"iso_3166_1": "US"}],
        "credits": {
            "cast": [{"id": 5000 + tmdb_id, "name": f"Star {tmdb_id}", "order": 0}],
            "crew": [{"id": zlib.crc32(director.encode()), "name": director, "job": "Director"}],
        },
    }


@pytest.fixture
def movielens_tmdb(tmp_path: Path) -> tuple[Path, Path]:
    """Seven films (one without TMDb details), two users with timed ratings."""
    ml = tmp_path / "ml"
    ml.mkdir()
    films = {  # movieId → (tmdbId, director, votes, genre)
        "1": (101, "Ann Director", 9000, "Drama"),
        "2": (102, "Ann Director", 8000, "Drama"),
        "3": (103, "Bo Director", 7000, "Comedy"),
        "4": (104, "Bo Director", 6000, "Comedy"),
        "5": (105, "Cy Director", 5000, "Horror"),
        "6": (106, "Cy Director", 100, "Horror"),  # least voted: outside a 5-title catalog
        "7": (107, "Gone Director", 1, "Drama"),  # TMDb returns 404
    }
    (ml / "links.csv").write_text(
        "movieId,imdbId,tmdbId\n" + "".join(f"{m},0,{t}\n" for m, (t, *_r) in films.items()),
        encoding="utf-8",
    )
    (ml / "movies.csv").write_text(
        "movieId,title,genres\n" + "".join(f"{m},Film {m} (2001),Drama\n" for m in films),
        encoding="utf-8",
    )
    # user 1: train = 1,2,6,3 (oldest first); test = 4 (liked), 5 (disliked)
    # user 2: train = 3,4,7,1; test = 2 (liked), 6 (liked but outside the catalog)
    ratings = [
        ("1", "1", 5.0, 1),
        ("1", "2", 4.5, 2),
        ("1", "6", 1.0, 3),
        ("1", "3", 3.0, 4),
        ("1", "4", 4.0, 5),
        ("1", "5", 1.5, 6),
        ("2", "3", 4.0, 1),
        ("2", "4", 4.5, 2),
        ("2", "7", 5.0, 3),
        ("2", "1", 2.0, 4),
        ("2", "2", 5.0, 5),
        ("2", "6", 4.0, 6),
    ]
    (ml / "ratings.csv").write_text(
        "userId,movieId,rating,timestamp\n"
        + "".join(f"{u},{m},{r},{ts}\n" for u, m, r, ts in ratings),
        encoding="utf-8",
    )
    cache = tmp_path / "tmdb.jsonl"
    lines = []
    for _movie, (tmdb_id, director, votes, genre) in films.items():
        if tmdb_id == 107:
            lines.append({"tmdb_id": tmdb_id, "status": "not_found"})
        else:
            payload = _payload(tmdb_id, director=director, votes=votes, genre=genre)
            lines.append({"tmdb_id": tmdb_id, "status": "ok", "payload": payload})
    cache.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return ml, cache


def _load(paths: tuple[Path, Path], **kwargs: Any):  # noqa: ANN202
    ml, cache = paths
    return load_movielens_tmdb_dataset(
        ml, cache, min_ratings=4, test_fraction=0.34, catalog_size=5, **kwargs
    )


def test_loader_builds_production_features_and_a_production_sized_catalog(movielens_tmdb) -> None:
    dataset = _load(movielens_tmdb)
    names = {t.name for t in dataset.titles}
    assert names == {"Film 101", "Film 102", "Film 103", "Film 104", "Film 105"}
    assert [t.name for t in dataset.profile_only_titles] == ["Film 106"]
    first = next(t for t in dataset.titles if t.name == "Film 101")
    assert "person:director:ann director" in first.extra["feature_snapshot"]
    assert first.vote_count == 9000 and first.popularity == pytest.approx(90.0)
    assert dataset.notes["titles_with_tmdb_metadata"] == 6
    assert dataset.notes["ratings_with_metadata"] == dataset.notes["ratings_total"] - 1


def test_loader_stores_collections_and_catalog_percentiles_as_ingest_does(movielens_tmdb) -> None:
    dataset = _load(movielens_tmdb)
    by_name = {t.name: t for t in [*dataset.titles, *dataset.profile_only_titles]}
    assert by_name["Film 101"].extra[COLLECTION_KEY] == {"id": 900, "name": "Ann Saga"}
    assert by_name["Film 101"].collection == by_name["Film 102"].collection == 900
    assert COLLECTION_KEY not in by_name["Film 103"].extra
    # percent_rank over the catalog (5 titles), as the ingest SQL computes it.
    stored = {t.name: t.extra[CATALOG_PERCENTILES_KEY] for t in dataset.titles}
    assert stored["Film 101"] == {"popularity": 1.0, "vote_count": 1.0}
    assert stored["Film 103"] == {"popularity": 0.5, "vote_count": 0.5}
    assert stored["Film 105"] == {"popularity": 0.0, "vote_count": 0.0}
    # Profile-only titles are not candidates, so ingest's catalog does not include them.
    assert CATALOG_PERCENTILES_KEY not in by_name["Film 106"].extra


def test_production_passes_both_launch_checks_offline() -> None:
    """S2 (≤ 2 films per director) and "no famous title labelled Hidden gem"."""
    dataset = build_synthetic_dataset(n_titles=400, n_users=20, seed=5)
    run = run_evaluation(
        dataset,
        {
            "production": DEFAULT_STRATEGIES["production"],
            "hybrid_no_diversity": DEFAULT_STRATEGIES["hybrid_no_diversity"],
        },
        k=20,
    )
    production = run.summary["production"]
    assert production["director_over_limit"] == 0.0
    assert production["director_max"] <= 2.0
    assert production["gem_famous_share"] == 0.0
    # The ablation lifts every cap, so it still shows what the caps prevent.
    assert run.summary["hybrid_no_diversity"]["director_max"] > 2.0


def test_production_variant_overrides_caps_as_well_as_weights() -> None:
    variant = production_variant(max_per_director=999, sparse_negative=0.5)
    assert variant.max_per_director == 999 and variant.max_per_collection is None
    assert variant.weights.sparse_negative == 0.5
    assert variant.candidate_pool == "production"


def test_paired_deltas_against_another_run_pair_by_user() -> None:
    dataset = build_synthetic_dataset(n_titles=200, n_users=8, seed=3)
    strategies = {"production": DEFAULT_STRATEGIES["production"]}
    before = run_evaluation(dataset, strategies, k=10)
    after = run_evaluation(dataset, strategies, k=10)
    previous = {"user_ids": before.user_ids, "per_user": before.per_user}
    deltas = paired_deltas_against(after, previous, metrics=["recall@10"])
    delta, low, high, pairs = deltas["production"]["recall@10"]
    assert (delta, low, high, pairs) == (0.0, 0.0, 0.0, len(after.user_ids))
    with pytest.raises(ValueError, match="different users"):
        paired_deltas_against(after, {**previous, "user_ids": before.user_ids[::-1]}, metrics=[])


def test_loader_split_negatives_and_leak_free_popularity(movielens_tmdb) -> None:
    dataset = _load(movielens_tmdb)
    by_name = {t.name: t for t in [*dataset.titles, *dataset.profile_only_titles]}
    users = {u.user_id: u for u in dataset.users}

    u1 = users["ml-1"]
    assert u1.test_positive_ids == {by_name["Film 104"].id}
    assert u1.test_negative_ids == {by_name["Film 105"].id}
    # Film 106 was rated in training, so it still shapes the profile.
    assert by_name["Film 106"].id in {e[0] for e in u1.train_events}

    u2 = users["ml-2"]
    # Film 106 is outside the catalog: never a held-out target.
    assert u2.test_positive_ids == {by_name["Film 102"].id}

    # Held-out ratings do not count towards the in-domain popularity baseline.
    assert by_name["Film 105"].train_interactions == 0
    assert by_name["Film 104"].train_interactions == 1  # user 2's train only
    assert by_name["Film 101"].train_interactions == 2


def test_every_strategy_runs_on_the_tmdb_dataset(movielens_tmdb) -> None:
    dataset = _load(movielens_tmdb)
    strategies = {**DEFAULT_STRATEGIES, "popular_ml": rating_count_strategy}
    run = run_evaluation(dataset, strategies, k=3)
    assert set(run.summary) == set(strategies)
    production = run.summary["production"]
    for metric in ("recall@3", "director_max", "like_dislike_auc", "dislike_recall@3"):
        assert not math.isnan(production[metric]), metric
    assert math.isnan(run.summary["random"]["like_dislike_auc"])  # no scores to rank by


def test_pairwise_auc() -> None:
    assert M.pairwise_auc([3.0, 2.0], [1.0]) == 1.0
    assert M.pairwise_auc([1.0], [3.0, 2.0]) == 0.0
    assert M.pairwise_auc([2.0], [2.0]) == 0.5
    assert math.isnan(M.pairwise_auc([], [1.0]))


def test_paired_bootstrap_drops_missing_pairs_and_brackets_the_delta() -> None:
    delta, low, high, n = M.paired_bootstrap([0.1, 0.2, None, 0.3], [0.2, 0.3, 0.5, None])
    assert n == 2
    assert delta == pytest.approx(0.1)
    assert low == pytest.approx(0.1) and high == pytest.approx(0.1)
    assert M.paired_bootstrap([None], [1.0])[3] == 0


def test_production_pool_matches_candidate_generation() -> None:
    dataset = build_synthetic_dataset(n_titles=800, n_users=5, seed=3)
    user = dataset.users[0]
    ctx = _context(user, dataset)
    pool = ctx.production_pool(20)
    pool_ids = {ctx.channels.pool[i].id for i in pool}
    seen = {e[0] for e in user.train_events}

    assert not pool_ids & seen
    assert len(pool) < len(dataset.titles)
    popular = [t.id for t in sorted(dataset.titles, key=lambda t: t.popularity, reverse=True)]
    assert set(popular[:PROD_POPULAR_CANDIDATES]) - seen <= pool_ids

    ranked = make_ranking_strategy(candidate_pool="production")(user, dataset, 20)
    assert {item.title_id for item in ranked} <= pool_ids


def test_slate_metrics_on_synthetic_data() -> None:
    dataset = build_synthetic_dataset(n_titles=400, n_users=20, seed=5)
    run = run_evaluation(
        dataset,
        {
            "production": DEFAULT_STRATEGIES["production"],
            "no_negative": production_variant(sparse_negative=0.0),
        },
        k=20,
    )
    summary = run.summary["production"]
    assert 0 < summary["gem_labels"] <= MAX_GEM_LABELS
    assert 0.0 <= summary["gem_fame_pct"] <= 1.0
    assert summary["director_max"] >= 1.0
    deltas = paired_deltas(run, baseline="production", metrics=["recall@20"])
    assert set(deltas) == {"no_negative"}
    assert deltas["no_negative"]["recall@20"][3] == len(run.user_ids)


def test_cli_runs_sweeps_and_variants_with_paired_deltas(tmp_path: Path, capsys) -> None:
    from app.scripts.evaluate_recommender import main

    out = tmp_path / "metrics.json"
    code = main(
        [
            "-k", "5", "--users", "6", "--titles", "80", "--only-variants",
            "--sweep", "sparse_negative=0,0.5",
            "--variant", "proposal:sparse_negative=0.5,warm_popularity=0.05",
            "--json", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    written = json.loads(out.read_text())
    assert set(written["summary"]) == {
        "production",
        "production[sparse_negative=0]",
        "production[sparse_negative=0.5]",
        "proposal",
    }
    assert set(written["deltas"]) == set(written["summary"]) - {"production"}
    assert "vs `production`" in capsys.readouterr().out


def test_cli_compares_against_another_code_versions_run(tmp_path: Path, capsys) -> None:
    from app.scripts.evaluate_recommender import main

    base = ["-k", "5", "--users", "6", "--titles", "80", "--strategies", "production"]
    previous = tmp_path / "master.json"
    assert main([*base, "--json", str(previous)]) == 0
    written = json.loads(previous.read_text())
    assert len(written["per_user"]["production"]) == len(written["user_ids"]) > 0
    capsys.readouterr()

    assert main([*base, "--against", str(previous)]) == 0
    out = capsys.readouterr().out
    assert "master.json (same strategy)" in out
    assert "+0.0000 [+0.0000, +0.0000]" in out  # same code, same users: no difference
    with pytest.raises(SystemExit):
        main([*base[:1], "10", *base[2:], "--against", str(previous)])  # another slate size


def test_engagement_markdown_shows_rates_with_intervals() -> None:
    from datetime import UTC, datetime

    from app.scripts.engagement_report import EngagementReport, _with_rates, format_markdown, wilson

    row = _with_rates({"bucket": 1, "shown": 40, "acted": 10, "positive": 8, "negative": 2})
    report = EngagementReport(
        since=datetime(2026, 12, 1, tzinfo=UTC),
        until=datetime(2026, 12, 29, tzinfo=UTC),
        window_days=7,
        volume={"impressions": 50, "slates": 3, "users": 2},
        tables={"by_position": [row]},
    )
    text = format_markdown(report)
    assert "| 1 | 40 | 25.0% (14%–40%) |" in text
    assert "(no impressions in this period)" in text  # empty tables say so
    low, high = wilson(0, 10)
    assert low == 0.0 and 0.2 < high < 0.35
