"""The SMTP path that password reset and email verification both depend on.

It had no test at all, which meant the first real send happened in production —
for a message whose only purpose is to let someone back into their account.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest

from app.core.config import Settings
from app.infrastructure.email import LogEmailSender, SmtpEmailSender, get_email_sender


class FakeSMTP:
    """Records what a real server would have been told to do."""

    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[str] = []
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("close")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.logged_in = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        self.calls.append("send_message")
        self.sent.append(msg)


class ExplodingSMTP(FakeSMTP):
    def send_message(self, msg: EmailMessage) -> None:
        raise smtplib.SMTPRecipientsRefused({"a@b.com": (550, b"no such user")})


@pytest.fixture(autouse=True)
def reset_instances():
    FakeSMTP.instances = []
    yield
    FakeSMTP.instances = []


def _settings(**overrides) -> Settings:
    data = {
        "jwt_secret": "unit-test-secret-key-at-least-32-chars!",
        "database_url": "postgresql+asyncpg://u:p@localhost/x",
        "smtp_host": "smtp.example.com",
        "smtp_user": "mailer@example.com",
        "smtp_password": "hunter2",
        "smtp_from": "noreply@cinetaste.app",
    }
    data.update(overrides)
    return Settings(**data)


@pytest.mark.asyncio
async def test_starttls_is_negotiated_on_the_submission_port(monkeypatch) -> None:
    """Port 587 starts in the clear; without STARTTLS the password goes plaintext."""
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender(_settings(smtp_port=587)).send(
        to="user@example.com", subject="Reset", text_body="link"
    )

    server = FakeSMTP.instances[0]
    assert server.calls.index("starttls") < server.calls.index("login")
    assert server.calls.index("login") < server.calls.index("send_message")


@pytest.mark.asyncio
async def test_implicit_tls_port_does_not_call_starttls(monkeypatch) -> None:
    """465 is TLS from the first byte; STARTTLS there is a protocol error."""
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    await SmtpEmailSender(_settings(smtp_port=465)).send(
        to="user@example.com", subject="Reset", text_body="link"
    )

    server = FakeSMTP.instances[0]
    assert "starttls" not in server.calls
    assert server.port == 465


@pytest.mark.asyncio
async def test_starttls_can_be_turned_off_but_login_still_happens(monkeypatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender(_settings(smtp_port=587, smtp_use_tls=False)).send(
        to="user@example.com", subject="Reset", text_body="link"
    )

    server = FakeSMTP.instances[0]
    assert "starttls" not in server.calls
    assert server.logged_in == ("mailer@example.com", "hunter2")


@pytest.mark.asyncio
async def test_anonymous_relay_skips_login(monkeypatch) -> None:
    """A local relay with no credentials must not be sent an empty LOGIN."""
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender(_settings(smtp_user="", smtp_password="")).send(
        to="user@example.com", subject="Reset", text_body="link"
    )

    assert "login" not in FakeSMTP.instances[0].calls


@pytest.mark.asyncio
async def test_the_message_carries_the_addresses_and_body(monkeypatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender(_settings()).send(
        to="user@example.com", subject="Reset your password", text_body="https://app/reset?token=x"
    )

    msg = FakeSMTP.instances[0].sent[0]
    assert msg["To"] == "user@example.com"
    assert msg["From"] == "noreply@cinetaste.app"
    assert msg["Subject"] == "Reset your password"
    assert "https://app/reset?token=x" in msg.get_content()


@pytest.mark.asyncio
async def test_from_falls_back_to_the_smtp_user(monkeypatch) -> None:
    """Providers reject a From they do not own; the account address is the safe default."""
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender(_settings(smtp_from="")).send(
        to="user@example.com", subject="Reset", text_body="link"
    )

    assert FakeSMTP.instances[0].sent[0]["From"] == "mailer@example.com"


@pytest.mark.asyncio
async def test_a_refused_recipient_propagates(monkeypatch) -> None:
    """AuthService turns this into a 503 rather than claiming the mail was sent."""
    monkeypatch.setattr(smtplib, "SMTP", ExplodingSMTP)
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        await SmtpEmailSender(_settings()).send(
            to="user@example.com", subject="Reset", text_body="link"
        )


def test_sender_selection_follows_configuration() -> None:
    assert isinstance(get_email_sender(_settings()), SmtpEmailSender)
    assert isinstance(get_email_sender(_settings(smtp_host="")), LogEmailSender)
    assert isinstance(get_email_sender(_settings(smtp_host="   ")), LogEmailSender)


@pytest.mark.asyncio
async def test_the_log_sender_never_writes_the_body(caplog) -> None:
    """Message bodies are one-time links. A log drain is not where they belong."""
    import logging

    with caplog.at_level(logging.INFO, logger="app.infrastructure.email"):
        await LogEmailSender().send(
            to="user@example.com", subject="Reset", text_body="https://app/reset?token=SECRET"
        )

    assert "SECRET" not in caplog.text
    assert "no_smtp_configured" in caplog.text
