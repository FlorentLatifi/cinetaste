"""Outbound email abstraction.

- No SMTP configured → nothing is sent (local/test get the reset token in the
  API response instead). Message bodies are never logged: they contain
  one-time reset links.
- SMTP_* set → stdlib SMTP (STARTTLS when port 587), run in a worker thread so
  a slow mail server doesn't block the event loop.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from anyio import to_thread

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, text_body: str) -> None: ...


class LogEmailSender:
    """No SMTP configured: record that an email was suppressed, nothing more."""

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        logger.info("email_not_sent reason=no_smtp_configured subject=%s", subject)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        await to_thread.run_sync(self._send_sync, to, subject, text_body)

    def _send_sync(self, to: str, subject: str, text_body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._settings.smtp_from or self._settings.smtp_user
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
        logger.info("email_sent subject=%s", subject)


def get_email_sender(settings: Settings) -> EmailSender:
    if settings.email_configured:
        return SmtpEmailSender(settings)
    return LogEmailSender()
