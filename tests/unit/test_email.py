"""notify/email.py: message building, SMTP conversation, outbox."""

from __future__ import annotations

import email
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tourgen.config import Settings
from tourgen.notify.email import (
    OutboxEmailSender,
    SmtpEmailSender,
    build_tour_email,
    sender_for,
    valid_email,
)


class RecordingSMTP:
    instances: ClassVar[list[RecordingSMTP]] = []

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls: list[str] = []
        self.sent: list[EmailMessage] = []
        RecordingSMTP.instances.append(self)

    def __enter__(self) -> RecordingSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: object = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, msg: EmailMessage) -> None:
        self.calls.append("send")
        self.sent.append(msg)


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(smtp_host="smtp.example.com", smtp_user="me@example.com",
                                smtp_password="secret")  # fmt: skip
    base.update(kw)
    return Settings(**base)


def _message(tmp_path: Path) -> EmailMessage:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x01mp4")
    return build_tour_email("me@example.com", ["a@x.com", "b@y.org"], "Tour", "Hello",
                            [("tour_16x9.mp4", video)])  # fmt: skip


def test_valid_email() -> None:
    assert valid_email("a.b+c@site.co.uk")
    assert not valid_email("nope") and not valid_email("a@b") and not valid_email("a b@c.de")


def test_build_message_attaches_video(tmp_path: Path) -> None:
    msg = _message(tmp_path)
    parsed = email.message_from_bytes(bytes(msg))
    assert parsed["To"] == "a@x.com, b@y.org"
    parts = [p for p in parsed.walk() if p.get_filename()]
    assert parts[0].get_filename() == "tour_16x9.mp4"
    assert parts[0].get_content_type() == "video/mp4"
    assert parts[0].get_payload(decode=True) == b"\x00\x01mp4"


def test_smtp_starttls_login_send(tmp_path: Path) -> None:
    RecordingSMTP.instances.clear()
    SmtpEmailSender(_settings(), smtp_factory=RecordingSMTP).send(_message(tmp_path))
    server = RecordingSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.example.com", 587)
    assert server.calls == ["starttls", "login:me@example.com", "send", "quit"]


def test_smtp_port_465_uses_implicit_tls() -> None:
    sender = SmtpEmailSender(_settings(smtp_port=465))
    assert sender._factory is smtplib.SMTP_SSL


def test_smtp_requires_configuration() -> None:
    with pytest.raises(ValueError):
        SmtpEmailSender(Settings(smtp_host=None))


def test_outbox_and_selection(tmp_path: Path) -> None:
    outbox = OutboxEmailSender(tmp_path / "outbox")
    outbox.send(_message(tmp_path))
    files = list((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 1 and b"Subject: Tour" in files[0].read_bytes()
    assert isinstance(sender_for(_settings(), tmp_path, outbox_only=True), OutboxEmailSender)
    assert isinstance(sender_for(_settings(), tmp_path, outbox_only=False), SmtpEmailSender)
