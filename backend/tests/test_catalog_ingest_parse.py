"""What ingest keeps from a TMDb payload's cast roles (no database)."""

from __future__ import annotations

from typing import Any

from app.application.catalog_ingest import CHARACTER_MAX_LENGTH, _Detail


def _role(character: Any) -> Any:
    payload = {"id": 1, "credits": {"cast": [{"id": 10, "name": "A", "character": character}]}}
    [credit] = _Detail.parse("movie", payload).credits
    return credit.character


def test_a_role_that_fits_is_kept_exactly() -> None:
    fits = "r" * CHARACTER_MAX_LENGTH
    assert _role(fits) == fits
    assert _role("Tyler Durden") == "Tyler Durden"


def test_a_role_too_long_for_its_column_is_cut_with_an_ellipsis() -> None:
    long = "Segment role / " * 40
    cut = _role(long)
    assert len(cut) == CHARACTER_MAX_LENGTH
    assert cut.endswith("…") and long.startswith(cut[:-1])


def test_trailing_space_is_not_left_before_the_ellipsis() -> None:
    cut = _role("x" * (CHARACTER_MAX_LENGTH - 2) + " " + "y" * 10)
    assert cut == "x" * (CHARACTER_MAX_LENGTH - 2) + "…"


def test_a_missing_role_stays_missing() -> None:
    assert _role(None) is None
    assert _role("") == ""
