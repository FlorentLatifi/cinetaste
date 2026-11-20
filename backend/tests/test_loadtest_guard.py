"""The load-test tools must never be aimed at a real deployment."""

from __future__ import annotations

import pytest

from tools.local_only import assert_local_target, is_local_target


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://api:8000",
        "http://host.docker.internal:8000",
        "http://192.168.1.20:8000",
        "http://10.0.0.5",
        "localhost:8000",
    ],
)
def test_local_and_private_targets_are_allowed(url: str) -> None:
    assert is_local_target(url)
    assert_local_target(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://cinetaste-api.onrender.com",
        "https://example.com",
        "http://8.8.8.8",
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com",
        "https://user@cinetaste-api.onrender.com",
        "",
    ],
)
def test_everything_else_is_refused(url: str) -> None:
    assert not is_local_target(url)
    with pytest.raises(SystemExit):
        assert_local_target(url)
