"""Behavioural invariants of the recommender.

Each test pins a property the product promises (the profile reflects the user's
*current* opinion, slates are diverse, explanations only cite real evidence).
Several reproduce bugs found in the audit and would fail on the old code.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.taste_summary import merge_import_overlay, rank_features
from app.domain.taste_signals import (
    FEED_EXCLUDE_STATES,
    decay_factor,
    effective_title_signals,
    resolve_title_state,
    weight_for,
)
from app.recommendation.embeddings import (
    PersonSignal,
    cosine,
    features_from_title,
    tones_from_keywords,
)
from app.recommendation.explanations import build_anchor_from_title, build_reasons
from app.recommendation.pipeline import (
    collection_cap_for,
    collection_of,
    directors_of,
    primary_genre,
    rank_titles,
)
from app.recommendation.profile import ProfileTitle, build_profile
from tests.conftest import FakeTitle

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _event(title_id, event_type: str, minutes: int):
    return (title_id, event_type, weight_for(event_type), T0 + timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# One effective signal per title
# ---------------------------------------------------------------------------


def test_latest_opinion_wins_like_then_dislike_is_a_dislike() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "like", 0), _event(t, "dislike", 5)])
    assert signals[t].event_type == "dislike"
    assert signals[t].weight == pytest.approx(weight_for("dislike"))


def test_weaker_tier_does_not_override_an_opinion() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "rate_4", 0), _event(t, "watched", 5)])
    assert signals[t].event_type == "rate_4"


def test_repeated_implicit_events_count_once() -> None:
    t = uuid4()
    signals = effective_title_signals([_event(t, "view", i) for i in range(20)])
    assert signals[t].weight == pytest.approx(weight_for("view"))


def test_clear_removes_everything_before_it() -> None:
    t = uuid4()
    signals = effective_title_signals(
        [_event(t, "rate_4", 0), _event(t, "clear", 1)]
    )
    assert t not in signals
    signals = effective_title_signals(
        [_event(t, "rate_4", 0), _event(t, "clear", 1), _event(t, "rate_1", 2)]
    )
    assert signals[t].event_type == "rate_1"


def test_rating_survives_watchlist_in_profile_and_state() -> None:
    t = uuid4()
    seq = ["rate_4", "watchlist"]
    signals = effective_title_signals([_event(t, e, i) for i, e in enumerate(seq)])
    assert signals[t].event_type == "rate_4"
    assert resolve_title_state(seq).rating == "rate_4"


def test_detail_page_watchlist_removal_cannot_erase_a_rating() -> None:
    """rate_4 → watchlist → (UI removes the watchlist entry).

    The page no longer offers the watchlist toggle on a rated title, so the
    only events the UI path produces are the rating and the save; the rating
    is what both the profile and the stored state keep. ``clear`` is still an
    explicit reset (History "Clear"), and that is the only way it goes.
    """
    t = uuid4()
    ui_path = ["rate_4", "watchlist"]
    assert resolve_title_state(ui_path).state == "like"
    assert effective_title_signals([_event(t, e, i) for i, e in enumerate(ui_path)])[
        t
    ].event_type == "rate_4"
    explicit_reset = [*ui_path, "clear"]
    assert resolve_title_state(explicit_reset).state == "none"
    assert t not in effective_title_signals(
        [_event(t, e, i) for i, e in enumerate(explicit_reset)]
    )


def test_rated_title_marked_watched_keeps_its_rating() -> None:
    assert resolve_title_state(["rate_4", "watched"]).rating == "rate_4"


def test_rated_title_stays_out_of_for_you_after_havent_seen() -> None:
    t = uuid4()
    seq = ["rate_3", "haven't_seen"]
    assert resolve_title_state(seq).state in FEED_EXCLUDE_STATES
    # Still an explain anchor, so it must not be recommended back.
    assert effective_title_signals([_event(t, e, i) for i, e in enumerate(seq)])[
        t
    ].event_type == "rate_3"


def test_rating_after_watchlist_or_clear_takes_over() -> None:
    assert resolve_title_state(["watchlist", "rate_3"]).state == "like"
    assert resolve_title_state(["clear", "rate_1"]) == resolve_title_state(["rate_1"])


def test_havent_seen_carries_no_signal() -> None:
    t = uuid4()
    assert effective_title_signals([_event(t, "haven't_seen", 0)]) == {}


def test_time_decay_halves_weight_after_one_half_life() -> None:
    t = uuid4()
    old = (t, "rate_3", 1.0, T0)
    signals = effective_title_signals([old], now=T0 + timedelta(days=365), half_life_days=365)
    assert signals[t].weight == pytest.approx(0.5, rel=1e-6)
    assert signals[t].raw_weight == 1.0
    assert decay_factor(10, None) == 1.0
    assert decay_factor(10, 0) == 1.0


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------


def _profile_title(fake: FakeTitle) -> ProfileTitle:
    return ProfileTitle(
        id=fake.id,
        name=fake.name,
        year=fake.release_year,
        feature_snapshot=fake.extra["feature_snapshot"],
        embedding=fake.embedding,
    )


def test_dislikes_only_profile_has_no_vector() -> None:
    hated = FakeTitle(name="Bad", genres=["Horror"], keywords=["gore"])
    signals = effective_title_signals([_event(hated.id, "rate_1", 0)])
    built = build_profile(signals.values(), {hated.id: _profile_title(hated)})
    assert built.vector is None
    assert built.features["genre:horror"] < 0


def test_vector_is_built_from_positive_signals_only() -> None:
    liked = FakeTitle(name="Liked", genres=["Science Fiction"], keywords=["space"])
    hated = FakeTitle(name="Hated", genres=["Comedy"], keywords=["wedding"])
    signals = effective_title_signals(
        [_event(liked.id, "rate_4", 0), _event(hated.id, "rate_1", 1)]
    )
    built = build_profile(
        signals.values(),
        {liked.id: _profile_title(liked), hated.id: _profile_title(hated)},
    )
    assert built.vector is not None
    assert cosine(built.vector, liked.embedding) == pytest.approx(1.0, abs=1e-6)


def test_anchors_come_from_strong_positive_opinions() -> None:
    fav = FakeTitle(name="Fav", genres=["Drama"])
    meh = FakeTitle(name="Meh", genres=["Drama"])
    signals = effective_title_signals(
        [_event(fav.id, "rate_4", 0), _event(meh.id, "rate_2", 1)]
    )
    built = build_profile(
        signals.values(), {fav.id: _profile_title(fav), meh.id: _profile_title(meh)}
    )
    assert [a["name"] for a in built.anchors] == ["Fav"]


# ---------------------------------------------------------------------------
# Ranking: diversity, exploration, cold start
# ---------------------------------------------------------------------------


def _drama_heavy_catalog() -> list[FakeTitle]:
    dramas = [
        FakeTitle(
            name=f"Drama {i}",
            genres=["Drama"],
            keywords=["family"],
            people=[PersonSignal(f"Dir{i % 7}", "director")],
            vote_average=6.0 + (i % 7) * 0.1,
            popularity=5.0 + i,
        )
        for i in range(60)
    ]
    comedies = [
        FakeTitle(name=f"Comedy {i}", genres=["Comedy"], keywords=["satire"], vote_average=6.0)
        for i in range(10)
    ]
    return dramas + comedies


def _profile_from(titles: list[FakeTitle]) -> tuple[list[float], dict[str, float]]:
    signals = effective_title_signals([_event(t.id, "rate_4", i) for i, t in enumerate(titles)])
    built = build_profile(signals.values(), {t.id: _profile_title(t) for t in titles})
    assert built.vector is not None
    return built.vector, built.features


def test_genre_cap_survives_backfill() -> None:
    """Audit repro: the old backfill loop produced a 20/20 Drama slate."""
    catalog = _drama_heavy_catalog()
    liked = catalog[:5]
    vector, features = _profile_from(liked)
    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in liked},
        slate_size=20,
        mmr_lambda=0.7,
        exploration_slots=3,
    )
    by_id = {t.id: t for t in catalog}
    counts = Counter(primary_genre(by_id[r.title_id]) for r in ranked)
    assert len(ranked) == 20
    # Only two genres exist, so the cap is raised evenly: comedies are not
    # crowded out by higher-scoring dramas.
    assert counts == {"drama": 10, "comedy": 10}


def test_cap_relaxes_only_when_catalog_has_one_genre() -> None:
    catalog = [FakeTitle(name=f"D{i}", genres=["Drama"]) for i in range(30)]
    ranked = rank_titles(
        user_vector=list(catalog[0].embedding),
        user_features={"genre:drama": 2.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=10,
        mmr_lambda=0.7,
        exploration_slots=2,
    )
    assert len(ranked) == 10


def _mixed_catalog() -> list[FakeTitle]:
    genres = ["Drama", "Comedy", "Thriller", "Horror", "Documentary", "Animation"]
    return [
        FakeTitle(
            name=f"{genres[i % len(genres)]} {i}",
            genres=[genres[i % len(genres)]],
            keywords=[f"kw{i % 9}"],
            vote_average=7.0 + (i % 5) * 0.2,
            popularity=10.0 + i,
        )
        for i in range(120)
    ]


def test_exploration_respects_slot_count_and_is_labelled() -> None:
    catalog = _mixed_catalog()
    drama_fans = [t for t in catalog if t.name.startswith("Drama")][:4]
    vector, features = _profile_from(drama_fans)
    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in drama_fans},
        slate_size=20,
        mmr_lambda=0.7,
        exploration_slots=3,
    )
    exploration = [
        i for i, r in enumerate(ranked) if any(reason.code == "discovery" for reason in r.reasons)
    ]
    assert 1 <= len(exploration) <= 3
    assert 0 not in exploration  # the first card is always the best match


def test_dislike_heavy_user_is_treated_as_cold_start() -> None:
    catalog = [
        FakeTitle(name="Blockbuster", genres=["Action"], popularity=300.0, vote_average=6.5),
        FakeTitle(name="Niche", genres=["Documentary"], popularity=2.0, vote_average=6.5),
        FakeTitle(name="Scary", genres=["Horror"], popularity=250.0, vote_average=6.5),
    ]
    ranked = rank_titles(
        user_vector=None,
        user_features={"genre:horror": -3.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=3,
        mmr_lambda=1.0,
        exploration_slots=0,
    )
    assert ranked[0].title_id == catalog[0].id


# ---------------------------------------------------------------------------
# Features and explanations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyword", ["dwarf", "award ceremony", "warehouse", "intense rivalry"])
def test_tone_rules_match_whole_words_only(keyword: str) -> None:
    assert tones_from_keywords([keyword]) == {}


def test_tone_rules_still_match_real_phrases() -> None:
    assert "war" in tones_from_keywords(["world war ii"])
    assert "horror" in tones_from_keywords(["body horror"])


def test_keyword_features_are_labelled_as_themes() -> None:
    chips = rank_features({"kw:heist": 1.2}, positive=True)
    assert chips[0].family == "kw"
    assert chips[0].label.startswith("Theme")


def test_import_maps_legacy_keyword_prefix() -> None:
    overlay = merge_import_overlay(
        None, likes=[{"key": "keyword:heist", "weight": 1.0}], dislikes=[]
    )
    assert "kw:heist" in overlay


def _anchor(name: str, event_type: str) -> dict:
    return build_anchor_from_title(
        title_id=uuid4(),
        name=name,
        event_type=event_type,
        weight=weight_for(event_type),
        feature_snapshot={"person:director:ava voss": 2.2, "genre:thriller": 1.0},
    )


def _reasons_for(anchor: dict) -> list:
    candidate = features_from_title(
        genres=["Thriller"],
        keywords=[],
        people=[PersonSignal("Ava Voss", "director")],
        release_year=2020,
        runtime=110,
        media_type="movie",
    )
    return build_reasons(
        user_features={"person:director:ava voss": 2.0, "genre:thriller": 1.0},
        explain_memory={"anchors": [anchor]},
        title_name="Candidate",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Thriller"],
        similarity=0.5,
    )


def test_explanation_says_rated_only_for_ratings() -> None:
    rated = _reasons_for(_anchor("Rated One", "rate_4"))[0].message
    liked = _reasons_for(_anchor("Liked One", "like"))[0].message
    assert "rated Rated One highly" in rated
    assert "liked Liked One" in liked
    assert "rated" not in liked


def test_explanations_do_not_claim_unmodelled_pacing() -> None:
    candidate = features_from_title(
        genres=["Comedy"],
        keywords=["satire"],
        people=[],
        release_year=2020,
        runtime=100,
        media_type="movie",
    )
    reasons = build_reasons(
        user_features={"tone:satirical": 2.0, "genre:comedy": 1.5, "kw:satire": 1.0},
        explain_memory={"anchors": []},
        title_name="Satire",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Comedy"],
        similarity=0.4,
    )
    assert reasons
    assert not any("pacing" in r.message for r in reasons)


def test_synopsis_filler_words_do_not_create_similarity() -> None:
    """Hashing "the", "of", "young"... made unrelated titles look alike."""
    from app.recommendation.embeddings import build_title_embedding, content_tokens

    assert content_tokens("The story of a young man who must find his way in the world") == []
    assert content_tokens("A detective hunts a smuggler through the harbor") == [
        "detective",
        "hunts",
        "smuggler",
        "harbor",
    ]
    common = dict(
        genres=["Drama"],
        keywords=[],
        people=[],
        media_type="movie",
        release_year=2010,
        runtime=100,
    )
    a = build_title_embedding(name="A", overview="the story of a young man and his life", **common)
    b = build_title_embedding(name="B", overview="the story of a young woman and her world", **common)
    # Only genre/decade/runtime/format remain in common; synopsis filler adds nothing.
    assert cosine(a, b) == pytest.approx(1.0)
    # Real synopsis words do separate titles that share only coarse metadata.
    c = build_title_embedding(name="C", overview="a detective hunts smugglers", **common)
    d = build_title_embedding(name="D", overview="astronauts repair a failing station", **common)
    assert cosine(c, d) < cosine(a, b) - 0.05


def test_warm_popularity_prior_is_off_by_default_and_opt_in() -> None:
    """The prior trades novelty for accuracy, so production leaves it off."""
    from app.recommendation.pipeline import DEFAULT_WEIGHTS, RankingWeights

    assert DEFAULT_WEIGHTS.warm_popularity == 0.0
    # Two equally relevant titles (same features, same vector) that differ only in popularity.
    niche = FakeTitle(name="Niche", genres=["Drama"], keywords=["family"], popularity=2.0)
    hit = FakeTitle(name="Hit", genres=["Drama"], keywords=["family"], popularity=400.0)
    hit.embedding = list(niche.embedding)
    liked = FakeTitle(name="Liked", genres=["Drama"], keywords=["family"])
    vector, features = _profile_from([liked])

    def top(weights: RankingWeights):
        ranked = rank_titles(
            user_vector=vector,
            user_features=features,
            titles=[niche, hit],
            exclude_ids=set(),
            slate_size=1,
            mmr_lambda=1.0,
            exploration_slots=0,
            weights=weights,
        )
        return ranked[0].title_id

    assert top(RankingWeights(warm_popularity=0.5)) == hit.id
    # Off: popularity plays no part for an established user; input order breaks the tie.
    assert top(DEFAULT_WEIGHTS) == niche.id


# ---------------------------------------------------------------------------
# Per-director cap and "hidden gem" labels
# ---------------------------------------------------------------------------


def _nolan_heavy_catalog() -> list[FakeTitle]:
    """Production repro: a fan of one director got 6 of their films in 20 cards.

    Ten near-identical films by one director outscore everything else; plenty
    of good alternatives exist across genres.
    """
    nolan = [
        FakeTitle(
            name=f"Nolan {i}",
            genres=["Science Fiction" if i % 2 else "Thriller"],
            keywords=["mind-bending", "time"],
            people=[PersonSignal("Christopher Nolan", "director")],
            vote_average=8.0,
            popularity=40.0 + i,
        )
        for i in range(10)
    ]
    genres = ["Science Fiction", "Thriller", "Drama", "Mystery", "Adventure"]
    others = [
        FakeTitle(
            name=f"Other {i}",
            genres=[genres[i % len(genres)]],
            keywords=["mind-bending" if i % 3 == 0 else f"kw{i}"],
            people=[PersonSignal(f"Director {i}", "director")],
            vote_average=7.0 + (i % 4) * 0.3,
            popularity=5.0 + i,
        )
        for i in range(60)
    ]
    return nolan + others


@pytest.mark.parametrize("exploration_slots", [0, 3])
def test_no_slate_has_more_than_two_films_by_one_director(exploration_slots: int) -> None:
    catalog = _nolan_heavy_catalog()
    liked = catalog[:3]  # loved three Nolan films
    vector, features = _profile_from(liked)
    assert features["person:director:christopher nolan"] > 0
    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in liked},
        slate_size=20,
        mmr_lambda=0.8,
        exploration_slots=exploration_slots,
    )
    by_id = {t.id: t for t in catalog}
    directors = Counter(d for r in ranked for d in directors_of(by_id[r.title_id]))
    assert len(ranked) == 20
    assert directors["christopher nolan"] == 2  # the taste still shows, twice
    assert max(directors.values()) <= 2


def test_co_directed_film_counts_for_each_director() -> None:
    coens = [
        FakeTitle(
            name=f"Coen {i}",
            genres=["Crime"],
            keywords=["dark comedy"],
            people=[PersonSignal("Joel Coen", "director"), PersonSignal("Ethan Coen", "director")],
            vote_average=7.8,
        )
        for i in range(6)
    ]
    solo_ethan = FakeTitle(
        name="Ethan Solo",
        genres=["Crime"],
        keywords=["dark comedy"],
        people=[PersonSignal("Ethan Coen", "director")],
    )
    others = [
        FakeTitle(name=f"O{i}", genres=["Drama", "Comedy", "Horror"][i % 3:][:1], vote_average=7.0)
        for i in range(30)
    ]
    catalog = [*coens, solo_ethan, *others]
    ranked = rank_titles(
        user_vector=list(coens[0].embedding),
        user_features={"person:director:joel coen": 2.5, "person:director:ethan coen": 2.5},
        titles=catalog,
        exclude_ids=set(),
        slate_size=20,
        mmr_lambda=0.9,
        exploration_slots=0,
    )
    by_id = {t.id: t for t in catalog}
    counts = Counter(d for r in ranked for d in directors_of(by_id[r.title_id]))
    assert counts["ethan coen"] <= 2 and counts["joel coen"] <= 2


def test_director_cap_relaxes_only_when_nothing_else_is_left() -> None:
    """A one-director catalog still fills the slate rather than returning 2 cards."""
    catalog = [
        FakeTitle(
            name=f"Auteur {i}",
            genres=[["Drama", "Comedy", "Thriller"][i % 3]],
            people=[PersonSignal("Auteur", "director")],
        )
        for i in range(12)
    ]
    ranked = rank_titles(
        user_vector=list(catalog[0].embedding),
        user_features={"person:director:auteur": 2.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=10,
        mmr_lambda=0.8,
        exploration_slots=2,
    )
    assert len(ranked) == 10


def _with_catalog_percentiles(title: FakeTitle, popularity: float, votes: float) -> FakeTitle:
    title.extra["catalog_percentiles"] = {"popularity": popularity, "vote_count": votes}
    return title


def test_famous_titles_are_never_labelled_hidden_gem() -> None:
    """Production repro: The Godfather Part II, Memento, Alien, The Prestige got the badge.

    Raw TMDb popularity was ~40 or below for all of them, under the old absolute
    threshold. In catalog percentiles they are near the top — by popularity, by
    vote count, or both — and must never be called hidden.
    """
    famous = [
        # (name, popularity pct, vote_count pct, raw popularity)
        ("The Godfather Part II", 0.97, 0.99, 31.0),
        ("Memento", 0.80, 0.98, 18.0),
        ("Alien", 0.85, 0.98, 22.0),
        ("The Prestige", 0.88, 0.99, 25.0),
        ("The Silence of the Lambs", 0.15, 0.99, 7.5),  # low trending score, huge audience
    ]
    catalog = [
        _with_catalog_percentiles(
            FakeTitle(
                name=name,
                genres=[["Drama", "Thriller", "Science Fiction", "Mystery", "Crime"][i]],
                keywords=["classic"],
                vote_average=8.4,
                popularity=raw,
            ),
            pop,
            votes,
        )
        for i, (name, pop, votes, raw) in enumerate(famous)
    ]
    gems = [
        _with_catalog_percentiles(
            FakeTitle(
                name=f"Small Film {i}",
                genres=[["Drama", "Thriller", "Horror", "Comedy"][i % 4]],
                keywords=["classic"],
                vote_average=7.6,
                popularity=3.0 + i,
            ),
            0.05 + 0.03 * i,
            0.15 + 0.02 * i,
        )
        for i in range(8)
    ]
    titles = [*catalog, *gems]
    for user_vector in (None, list(catalog[0].embedding)):  # cold and warm
        ranked = rank_titles(
            user_vector=user_vector,
            user_features={"kw:classic": 2.0, "genre:drama": 1.0},
            titles=titles,
            exclude_ids=set(),
            slate_size=len(titles),  # every title is shown, so every label is visible
            mmr_lambda=0.8,
            exploration_slots=0,
        )
        by_id = {t.id: t for t in titles}
        labelled = {
            by_id[r.title_id].name
            for r in ranked
            if any(reason.code == "hidden_gem" for reason in r.reasons)
        }
        assert {r.title_id for r in ranked} >= {t.id for t in catalog}  # famous ones shown
        assert labelled, "small, well-rated films should still get the badge"
        assert labelled.isdisjoint(name for name, *_ in famous)
        assert all(r.hidden_gem == (by_id[r.title_id].name in labelled) for r in ranked)


# ---------------------------------------------------------------------------
# Per-collection (franchise) cap
# ---------------------------------------------------------------------------

MATRIX = {"id": 2344, "name": "The Matrix Collection"}


def _in_collection(title: FakeTitle, collection: dict[str, object]) -> FakeTitle:
    title.extra["collection"] = dict(collection)
    return title


def _franchise_heavy_catalog() -> list[FakeTitle]:
    """Eight near-identical sequels outscore everything; plenty of alternatives exist.

    Every sequel has its own director, so the director cap cannot be what
    keeps them apart: only the collection cap can.
    """
    sequels = [
        _in_collection(
            FakeTitle(
                name=f"Matrix {i}",
                genres=["Science Fiction" if i % 2 else "Action"],
                keywords=["simulated reality", "chosen one"],
                people=[PersonSignal(f"Franchise Director {i}", "director")],
                vote_average=7.6,
                popularity=60.0 + i,
            ),
            MATRIX,
        )
        for i in range(10)
    ]
    genres = ["Science Fiction", "Action", "Thriller", "Drama", "Mystery"]
    others = [
        FakeTitle(
            name=f"Standalone {i}",
            genres=[genres[i % len(genres)]],
            keywords=["simulated reality" if i % 3 == 0 else f"kw{i}"],
            people=[PersonSignal(f"Director {i}", "director")],
            vote_average=7.0 + (i % 4) * 0.3,
            popularity=5.0 + i,
        )
        for i in range(70)
    ]
    return sequels + others


def _collection_counts(ranked: list, catalog: list[FakeTitle]) -> Counter[int]:
    by_id = {t.id: t for t in catalog}
    return Counter(c for r in ranked if (c := collection_of(by_id[r.title_id])) is not None)


@pytest.mark.parametrize("exploration_slots", [0, 3])
@pytest.mark.parametrize("slate_size", [10, 20, 40])
def test_no_slate_holds_more_than_n_titles_of_one_collection(
    slate_size: int, exploration_slots: int
) -> None:
    """A Matrix fan's slate shows the franchise, not the franchise's whole back catalog."""
    catalog = _franchise_heavy_catalog()
    liked = catalog[:2]  # loved the first two films
    vector, features = _profile_from(liked)
    rest = [t for t in catalog if t not in liked]

    # Without the cap the sequels really would crowd the slate (the repro holds).
    uncapped = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in liked},
        slate_size=slate_size,
        mmr_lambda=0.8,
        exploration_slots=exploration_slots,
        max_per_collection=999,
    )
    assert _collection_counts(uncapped, rest)[MATRIX["id"]] > collection_cap_for(slate_size)

    ranked = rank_titles(
        user_vector=vector,
        user_features=features,
        titles=catalog,
        exclude_ids={t.id for t in liked},
        slate_size=slate_size,
        mmr_lambda=0.8,
        exploration_slots=exploration_slots,
    )
    assert len(ranked) == slate_size
    # The taste still shows, but only up to the cap.
    assert _collection_counts(ranked, rest)[MATRIX["id"]] == collection_cap_for(slate_size)


def test_collection_cap_scales_with_slate_size() -> None:
    assert collection_cap_for(1) == 1
    assert collection_cap_for(10) == 1
    assert collection_cap_for(20) == 1
    assert collection_cap_for(40) == 2
    assert collection_cap_for(100) == 5


def test_collection_cap_relaxes_last_and_only_when_nothing_else_is_left() -> None:
    """Short catalog: another film by one director comes before another sequel.

    Six sequels (scored highest) and six films by one director, all one genre,
    for a slate of ten. Every cap has to give; the collection cap gives last.
    """
    sequels = [
        _in_collection(
            FakeTitle(
                name=f"Sequel {i}",
                genres=["Drama"],
                keywords=["saga"],
                people=[PersonSignal(f"Saga Director {i}", "director")],
            ),
            {"id": 77, "name": "Saga Collection"},
        )
        for i in range(6)
    ]
    auteur = [
        FakeTitle(
            name=f"Auteur {i}",
            genres=["Drama"],
            keywords=[f"kw{i}"],
            people=[PersonSignal("Auteur", "director")],
        )
        for i in range(6)
    ]
    catalog = [*sequels, *auteur]
    ranked = rank_titles(
        user_vector=list(sequels[0].embedding),
        user_features={"kw:saga": 2.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=10,
        mmr_lambda=0.8,
        exploration_slots=0,
    )
    assert len(ranked) == 10  # the slate is still filled
    by_id = {t.id: t for t in catalog}
    assert sum(by_id[r.title_id] in auteur for r in ranked) == 6
    assert _collection_counts(ranked, catalog)[77] == 4


def test_titles_without_collection_are_never_held_back() -> None:
    """Titles ingested before the field existed, malformed values, and TV: no cap."""
    catalog = [
        FakeTitle(name=f"T{i}", genres=[["Drama", "Comedy", "Thriller", "Horror"][i % 4]])
        for i in range(24)
    ]
    catalog[0].extra["collection"] = None
    catalog[1].extra["collection"] = {"name": "No id"}
    catalog[2].extra["collection"] = {"id": "not-a-number"}
    catalog[3].extra["collection"] = "2344"
    assert all(collection_of(t) is None for t in catalog)
    ranked = rank_titles(
        user_vector=list(catalog[0].embedding),
        user_features={"genre:drama": 1.0},
        titles=catalog,
        exclude_ids=set(),
        slate_size=20,
        mmr_lambda=0.8,
        exploration_slots=0,
    )
    assert len(ranked) == 20
