"""Unit tests for curated onboarding seed deck (no DB)."""

from datetime import date
from uuid import uuid4

from app.data.onboarding_seed import (
    all_seed_tmdb_ids,
    load_onboarding_seed_deck,
    order_titles_by_seed,
    pick_diverse_fallback,
    primary_seed_tmdb_ids,
)


def test_seed_deck_loads_and_has_primary_size() -> None:
    deck = load_onboarding_seed_deck()
    assert deck.version >= 1
    # Cold-start core: ~12–15 recognizable, diverse titles
    assert 12 <= len(deck.primary) <= 18
    assert len(deck.reserve) >= 20
    assert len(deck.ordered) == len(deck.primary) + len(deck.reserve)


def test_seed_ids_unique_and_stable() -> None:
    ids = all_seed_tmdb_ids()
    assert len(ids) == len(set(ids))
    primary = primary_seed_tmdb_ids()
    assert primary == ids[: len(primary)]
    # Spot-check well-known TMDb IDs
    assert 278 in primary  # Shawshank
    assert 129 in primary  # Spirited Away
    assert 496243 in primary  # Parasite


def test_primary_covers_diversity_axes() -> None:
    deck = load_onboarding_seed_deck()
    origins = {e.origin for e in deck.primary}
    decades = {e.decade for e in deck.primary if e.decade is not None}
    genres = {g for e in deck.primary for g in e.genres}
    tiers = {e.tier for e in deck.primary}
    polarizing = [e for e in deck.primary if e.polarizing]

    assert len(origins) >= 3  # not US-only
    assert min(decades) <= 1970
    assert max(decades) >= 2010
    assert len(genres) >= 8
    assert "iconic" in tiers
    assert "well_known" in tiers or "quality_gem" in tiers
    assert len(polarizing) >= 2


def test_order_titles_by_seed_preserves_curated_order() -> None:
    class T:
        def __init__(self, tmdb_id: int) -> None:
            self.external_tmdb_id = tmdb_id
            self.id = uuid4()

    titles = [T(680), T(278), T(129)]
    ordered = order_titles_by_seed(titles, [278, 129, 680, 999])
    assert [t.external_tmdb_id for t in ordered] == [278, 129, 680]


def test_pick_diverse_fallback_spreads_genre_and_decade() -> None:
    class G:
        def __init__(self, name: str) -> None:
            self.name = name

    class T:
        def __init__(
            self,
            genre: str,
            year: int,
            *,
            lang: str = "en",
            votes: int = 10000,
            rating: float = 8.0,
            pop: float = 40.0,
        ) -> None:
            self.id = uuid4()
            self.genres = [G(genre)]
            self.release_date = date(year, 1, 1)
            self.original_language = lang
            self.vote_count = votes
            self.vote_average = rating
            self.popularity = pop
            self.poster_path = "/x.jpg"

    # Many Action 2010s would dominate pure popularity; fallback should cap.
    pool = (
        [T("Action", 2015, votes=50000 - i, pop=90) for i in range(8)]
        + [T("Drama", 1995, votes=20000, rating=8.5) for _ in range(3)]
        + [T("Comedy", 2005, votes=15000) for _ in range(3)]
        + [T("Horror", 1982, votes=12000, rating=7.8) for _ in range(2)]
        + [T("Romance", 2001, lang="fr", votes=11000) for _ in range(2)]
    )
    picked = pick_diverse_fallback(pool, limit=8, max_per_genre=2, max_per_decade=3)
    assert len(picked) == 8
    genres = [t.genres[0].name for t in picked]
    assert len(set(genres)) >= 3
