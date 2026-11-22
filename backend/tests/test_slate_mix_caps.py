"""Language and TV caps sized to the user's taste mix.

Live, November 2026: four non-English favourites (one of them Spirited Away)
produced a guest slate with 11 of 20 Japanese titles, two of them anime TV
series, on a page that promises films. These pin the rules that stop it.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.taste_signals import EffectiveSignal
from app.recommendation.mix import TASTE_MIX_KEY, TasteMix, read_taste_mix, taste_mix
from app.recommendation.pipeline import (
    language_cap_for,
    language_caps_for,
    language_of,
    media_of,
    rank_titles,
    tv_cap_for,
    tv_cap_for_user,
)
from app.recommendation.profile import ProfileTitle, build_profile
from tests.conftest import FakeTitle


def _catalog(language: str, n: int, *, media_type: str = "movie", prefix: str = "") -> list[FakeTitle]:
    # Distinct genres/directors so only the language or TV cap can bind.
    genres = ["Drama", "Comedy", "Thriller", "Romance", "Crime", "Mystery", "Fantasy", "Action"]
    return [
        FakeTitle(
            name=f"{prefix}{language}-{i}",
            genres=[genres[i % len(genres)]],
            keywords=["family"],
            people=[(f"director {language} {prefix}{i}", "director")],
            original_language=language,
            media_type=media_type,
            vote_average=7.4,
            popularity=10.0 + i,
        )
        for i in range(n)
    ]


def _rank(titles: list[FakeTitle], mix: TasteMix | None, *, slate: int = 20, **kw):
    return rank_titles(
        user_vector=list(titles[0].embedding),
        user_features={"genre:drama": 2.0, "kw:family": 1.0},
        titles=titles,
        exclude_ids=set(),
        slate_size=slate,
        mmr_lambda=0.8,
        exploration_slots=0,
        taste_mix=mix,
        **kw,
    )


def test_caps_scale_with_slate_size() -> None:
    assert language_cap_for(20) == 8
    assert language_cap_for(5) == 3  # floor
    assert tv_cap_for(20) == 4
    assert tv_cap_for(12) == 2


def test_language_cap_follows_the_users_share() -> None:
    mix = TasteMix(languages={"en": 0.9, "ko": 0.1})
    caps = language_caps_for(mix, 20)
    assert caps == {"en": 18, "ko": 8}
    assert language_caps_for(TasteMix(), 20) is None  # no signal → no cap


def test_tv_cap_follows_the_users_share() -> None:
    assert tv_cap_for_user(TasteMix(media={"movie": 1.0}), 20) == 4
    assert tv_cap_for_user(TasteMix(media={"movie": 0.4, "tv": 0.6}), 20) == 12


def test_mixed_language_rater_gets_no_more_than_the_cap_of_one_language() -> None:
    titles = _catalog("ja", 25) + _catalog("ko", 6) + _catalog("fr", 6) + _catalog("pt", 6)
    mix = TasteMix(languages={"ja": 0.3, "ko": 0.3, "fr": 0.2, "pt": 0.2})
    slate = _rank(titles, mix)
    by_id = {t.id: t for t in titles}
    counts = Counter(by_id[item.title_id].original_language for item in slate)
    assert len(slate) == 20
    assert counts["ja"] <= language_cap_for(20)


def test_single_language_rater_keeps_a_single_language_slate() -> None:
    titles = _catalog("en", 25) + _catalog("ja", 25)
    slate = _rank(titles, TasteMix(languages={"en": 1.0}))
    by_id = {t.id: t for t in titles}
    # English is the user's own language: uncapped, so the ranking decides.
    english = sum(1 for item in slate if by_id[item.title_id].original_language == "en")
    assert english > language_cap_for(20)


def test_no_language_signal_means_no_language_cap() -> None:
    titles = _catalog("en", 25) + _catalog("ja", 25)
    capped_off = _rank(titles, TasteMix(), max_per_language=999)
    no_signal = _rank(titles, TasteMix())
    assert [i.title_id for i in no_signal] == [i.title_id for i in capped_off]
    # ...whereas a mixed rater with the same catalog is capped.
    mixed = _rank(titles, TasteMix(languages={"en": 0.5, "ja": 0.5}))
    by_id = {t.id: t for t in titles}
    english = sum(1 for item in mixed if by_id[item.title_id].original_language == "en")
    assert english <= 10  # 50% share of 20 → cap 10


def test_film_rater_gets_at_most_the_tv_cap() -> None:
    titles = _catalog("en", 25, media_type="tv", prefix="s") + _catalog("en", 25, prefix="f")
    slate = _rank(titles, TasteMix(languages={"en": 1.0}, media={"movie": 1.0}))
    by_id = {t.id: t for t in titles}
    tv = sum(1 for item in slate if by_id[item.title_id].media_type == "tv")
    assert tv <= tv_cap_for(20)


def test_max_tv_zero_is_films_only_until_films_run_out() -> None:
    titles = _catalog("en", 25, media_type="tv", prefix="s") + _catalog("en", 25, prefix="f")
    slate = _rank(titles, None, max_tv=0)
    by_id = {t.id: t for t in titles}
    assert all(by_id[item.title_id].media_type == "movie" for item in slate)
    # With too few films the slate still fills — series only as the last resort.
    short = _catalog("en", 25, media_type="tv", prefix="s") + _catalog("en", 5, prefix="f")
    filled = _rank(short, None, max_tv=0)
    assert len(filled) == 20


def test_caps_never_leave_a_slate_short() -> None:
    titles = _catalog("ja", 30)
    slate = _rank(titles, TasteMix(languages={"ja": 0.2, "ko": 0.8}))
    assert len(slate) == 20  # only Japanese exists: the cap relaxes


def test_language_and_media_of_read_snapshot_then_attributes() -> None:
    title = FakeTitle(name="X", genres=["Drama"], original_language="ko", media_type="tv")
    assert language_of(title) == "ko"
    assert media_of(title) == "tv"
    bare = type("Bare", (), {"extra": {}, "original_language": "FR", "media_type": None})()
    assert language_of(bare) == "fr"
    assert media_of(bare) == "movie"


def test_taste_mix_counts_positive_signal_only() -> None:
    mix = taste_mix(
        [
            ({"lang:en": 0.85, "media:movie": 0.6}, 1.55),
            ({"lang:ja": 0.85, "media:tv": 0.6}, 0.45),
            ({"lang:ko": 0.85, "media:movie": 0.6}, -0.9),  # a dislike says nothing here
        ]
    )
    assert mix.languages == pytest.approx({"en": 1.55 / 2.0, "ja": 0.45 / 2.0})
    assert mix.media == pytest.approx({"movie": 1.55 / 2.0, "tv": 0.45 / 2.0})


def test_build_profile_stores_the_mix_unclamped() -> None:
    """Thirty English favourites and three Korean ones: the stored lang: features
    are clamped per key, but the mix still says ~91% English."""
    now = datetime.now(UTC)
    titles: dict = {}
    signals = []
    for i, lang in enumerate(["en"] * 30 + ["ko"] * 3):
        tid = uuid4()
        titles[tid] = ProfileTitle(
            id=tid,
            name=f"t{i}",
            year=2010,
            feature_snapshot={f"lang:{lang}": 0.85, "media:movie": 0.6, "genre:drama": 1.0},
            embedding=None,
        )
        signals.append(
            EffectiveSignal(
                title_id=tid,
                event_type="rate_4",
                weight=1.55,
                raw_weight=1.55,
                created_at=now - timedelta(minutes=i),
            )
        )
    built = build_profile(signals, titles)
    assert built.mix.languages["en"] == pytest.approx(30 / 33)
    stored = built.features_with_memory()
    assert read_taste_mix(stored).languages["en"] == pytest.approx(30 / 33, abs=1e-3)
    # The clamped features alone would understate English.
    clamped = read_taste_mix({k: v for k, v in stored.items() if k != TASTE_MIX_KEY})
    assert clamped.languages["en"] < 0.6  # 0.53
