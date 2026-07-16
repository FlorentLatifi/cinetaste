"""Unit tests for rate-limit IP trust and readiness semantics (no live Redis/DB)."""

from unittest.mock import MagicMock

from app.core.middleware import client_ip


def test_client_ip_ignores_xff_when_not_trusted() -> None:
    request = MagicMock()
    request.headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}
    request.client.host = "9.9.9.9"
    assert client_ip(request, trust_x_forwarded_for=False) == "9.9.9.9"


def test_client_ip_uses_first_xff_when_trusted() -> None:
    request = MagicMock()
    request.headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}
    request.client.host = "9.9.9.9"
    assert client_ip(request, trust_x_forwarded_for=True) == "1.2.3.4"


def test_client_ip_falls_back_without_client() -> None:
    request = MagicMock()
    request.headers = {}
    request.client = None
    assert client_ip(request, trust_x_forwarded_for=False) == "unknown"
