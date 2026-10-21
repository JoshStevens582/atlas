"""Outbound plain-text email over SMTP (password reset, verification, etc.)."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from atlas.config import Settings

logger = logging.getLogger("atlas.smtp")


async def send_plain_email(
    settings: Settings,
    *,
    to_address: str,
    subject: str,
    body: str,
) -> bool:
    if not settings.smtp_host.strip():
        return False
    sender = settings.smtp_from.strip() or settings.smtp_username.strip()
    if not sender:
        logger.warning("SMTP missing smtp_from/smtp_username")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_address
    message.set_content(body)

    try:
        await asyncio.to_thread(_smtp_send, settings, message)
    except Exception as exc:
        logger.warning("SMTP send failed: %s", type(exc).__name__)
        return False
    return True


def _smtp_send(settings: Settings, message: EmailMessage) -> None:
    host = settings.smtp_host.strip()
    port = settings.smtp_port
    username = settings.smtp_username.strip()
    password = settings.smtp_password

    if settings.smtp_use_tls:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.starttls()
            if username:
                client.login(username, password)
            client.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=30) as client:
        if username:
            client.login(username, password)
        client.send_message(message)
