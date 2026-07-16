"""Outbound email abstraction.

- No SMTP configured → log-only sender (safe default for local/CI).
- SMTP_* set → stdlib SMTP (STARTTLS when port 587).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, text_body: str) -> None: ...


class LogEmailSender:
    """Dev/default: never leaves the process; logs the payload."""

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        logger.info(
            "email_log_only to=%s subject=%s body=%s",
            to,
            subject,
            text_body.replace("\n", " | "),
        )


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, to: str, subject: str, text_body: str) -> None:
        # Sync SMTP in a thread would be nicer; for low volume (password reset) blocking is OK.
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
        logger.info("email_sent to=%s subject=%s", to, subject)


def get_email_sender(settings: Settings) -> EmailSender:
    if (settings.smtp_host or "").strip():
        return SmtpEmailSender(settings)
    return LogEmailSender()
