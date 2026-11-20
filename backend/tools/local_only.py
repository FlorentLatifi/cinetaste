"""Refuse to aim load at anything that is not this machine or a private network.

Shared by the load-test tools. There is deliberately no override: a load test of the
production API is an outage you caused yourself (one Render worker, a free database),
and "just this once" is how that happens. Test a local stack or a private network.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Names a local docker compose stack answers to.
_LOCAL_NAMES = frozenset({"localhost", "api", "backend", "host.docker.internal"})


def is_local_target(url: str) -> bool:
    host = (urlparse(url if "://" in url else f"http://{url}").hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # any other name is a DNS name we cannot vouch for
    return address.is_loopback or address.is_private


def assert_local_target(url: str) -> None:
    if not is_local_target(url):
        raise SystemExit(
            f"refusing to load-test {url!r}: only localhost / private-network targets are "
            "allowed. Start the stack locally (docker compose up) and point at that."
        )
