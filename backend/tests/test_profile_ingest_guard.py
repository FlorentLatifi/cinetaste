"""The ingest profiler empties the catalog tables, so it must refuse any real database."""

from __future__ import annotations

import pytest

from tools.profile_catalog_ingest import _guard_database, _kind


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@localhost:5432/cinetaste_bench",
        "postgresql+asyncpg://u:p@127.0.0.1/cinetaste_test",
        "postgresql+asyncpg://u:p@db:5432/cinetaste_bench",
    ],
)
def test_a_local_throwaway_database_is_allowed(url: str) -> None:
    _guard_database(url)


@pytest.mark.parametrize(
    "url",
    [
        # The production shape: a hosted database, whatever its name.
        "postgresql+asyncpg://u:p@ep-cool-name-123.us-west-2.aws.neon.tech/neondb",
        "postgresql+asyncpg://u:p@ep-cool-name-123.us-west-2.aws.neon.tech/cinetaste_bench",
        # Local, but the database the dev server uses.
        "postgresql+asyncpg://u:p@localhost:5432/cinetaste",
        "postgresql+asyncpg://u:p@localhost.evil.com/cinetaste_bench",
    ],
)
def test_anything_else_is_refused(url: str) -> None:
    with pytest.raises(SystemExit):
        _guard_database(url)


def test_statements_are_grouped_by_verb_and_table() -> None:
    assert _kind("SELECT titles.id FROM titles WHERE ...") == "SELECT titles"
    assert _kind("INSERT INTO credits (id, title_id) VALUES ($1, $2)") == "INSERT INTO credits"
    assert _kind("DELETE FROM title_genres WHERE ...") == "DELETE FROM title_genres"
    assert _kind("SAVEPOINT sa_savepoint_1") == "SAVEPOINT"
