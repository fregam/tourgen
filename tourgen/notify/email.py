"""
FILE: tourgen/notify/email.py
ROLE: Build and send the "your tour is ready" email. The only module that talks SMTP.

HOW IT WORKS:
  - build_tour_email(): plain-text message with the videos attached as video/mp4.
  - SmtpEmailSender: STARTTLS on 587 (default) or implicit TLS on 465, optional login.
  - OutboxEmailSender: writes a .eml file instead of sending (tests, --email-outbox,
    or TOURGEN_EMAIL_OUTBOX_ONLY) — open it in any mail client to preview.
  - sender_for() chooses between them.

CONNECTS TO:
  - stages/notify.py; settings from config.Settings (TOURGEN_SMTP_*)

INVARIANTS / GOTCHAS:
  - Credentials only come from settings/.env; never logged, never written to job files.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Protocol

from tourgen.config import Settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(address: str) -> bool:
    return bool(EMAIL_RE.match(address))


def smtp_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and (settings.smtp_from or settings.smtp_user))


def build_tour_email(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, Path]],
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="tourgen.local")
    msg.set_content(body)
    for filename, path in attachments:
        msg.add_attachment(path.read_bytes(), maintype="video", subtype="mp4", filename=filename)
    return msg


class EmailSender(Protocol):
    transport: str

    def send(self, msg: EmailMessage) -> None: ...


class SmtpEmailSender:
    transport = "smtp"

    def __init__(self, settings: Settings, smtp_factory: Callable[..., Any] | None = None) -> None:
        if not smtp_configured(settings):
            raise ValueError("SMTP is not configured (set TOURGEN_SMTP_HOST and TOURGEN_SMTP_USER)")
        self.settings = settings
        implicit_tls = settings.smtp_port == 465
        default: Callable[..., Any] = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
        self._factory: Callable[..., Any] = smtp_factory or default
        self._implicit_tls = implicit_tls

    def send(self, msg: EmailMessage) -> None:
        s = self.settings
        context = ssl.create_default_context()
        kwargs: dict[str, Any] = {"timeout": 120}
        if self._implicit_tls:
            kwargs["context"] = context
        with self._factory(s.smtp_host, s.smtp_port, **kwargs) as server:
            if s.smtp_starttls and not self._implicit_tls:
                server.starttls(context=context)
            if s.smtp_user and s.smtp_password:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)


class OutboxEmailSender:
    transport = "outbox"

    def __init__(self, outbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir

    def send(self, msg: EmailMessage) -> None:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", str(msg["Message-ID"])).strip("_")
        (self.outbox_dir / f"{name}.eml").write_bytes(bytes(msg))


def sender_for(settings: Settings, outbox_dir: Path, outbox_only: bool) -> EmailSender:
    if outbox_only or settings.email_outbox_only:
        return OutboxEmailSender(outbox_dir)
    return SmtpEmailSender(settings)
