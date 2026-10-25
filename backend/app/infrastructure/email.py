"""Outbound email abstraction.

- No provider configured → nothing is sent (local/test get the reset token in
  the API response instead). Message bodies are never logged: they contain
  one-time reset links.
- BREVO_API_KEY set → Brevo's HTTPS API. Preferred, because some hosts (Render's
  free plan among them) block outbound SMTP ports and every send would fail.
- SMTP_* set → stdlib SMTP (STARTTLS when port 587), run in a worker thread so
  a slow mail server doesn't block the event loop.

A failed send is logged here with the provider and the reason, so the log line
says what to fix; the caller decides what the user is told.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

import httpx
from anyio import to_thread

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, text_body: str) -> None: ...


class EmailDeliveryError(RuntimeError):
    """The provider answered, and the answer was no."""


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


class LogEmailSender:
    """No provider configured: record that an email was suppressed, nothing more."""

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        logger.info("email_not_sent reason=no_smtp_configured subject=%s", subject)


class BrevoEmailSender:
    API_URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        name, address = parseaddr(self._settings.email_sender)
        sender = {"email": address, **({"name": name} if name else {})}
        payload = {
            "sender": sender,
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text_body,
        }
        headers = {"api-key": self._settings.brevo_api_key.strip(), "accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
                response = await client.post(self.API_URL, json=payload, headers=headers)
            if response.status_code >= 300:
                # Brevo's error body names the problem (unverified sender, bad
                # key) and never echoes the key back.
                raise EmailDeliveryError(f"status={response.status_code} body={response.text[:200]}")
        except Exception as exc:
            logger.error("email_send_failed provider=brevo reason=%s", _reason(exc))
            raise
        logger.info("email_sent provider=brevo subject=%s", subject)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        try:
            await to_thread.run_sync(self._send_sync, to, subject, text_body)
        except Exception as exc:
            # Authentication errors carry the server's reply, never the password.
            logger.error("email_send_failed provider=smtp reason=%s", _reason(exc))
            raise

    def _send_sync(self, to: str, subject: str, text_body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._settings.email_sender
        msg["To"] = to
        msg.set_content(text_body)

        host = self._settings.smtp_host
        port = int(self._settings.smtp_port)
        user = self._settings.smtp_user
        password = self._settings.smtp_password

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if self._settings.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        logger.info("email_sent provider=smtp subject=%s", subject)


def get_email_sender(settings: Settings) -> EmailSender:
    if settings.brevo_api_key.strip():
        return BrevoEmailSender(settings)
    if settings.smtp_host.strip():
        return SmtpEmailSender(settings)
    return LogEmailSender()
