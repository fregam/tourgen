"""The user flow: `tourgen run --url <listing> --email <me>` → videos arrive by email.

A local HTTP server plays the listing site (fixture photos + HTML + robots.txt);
Claude and Higgsfield are faked; email goes to a recording SMTP fake or the outbox.
"""

from __future__ import annotations

import email
import shutil
import smtplib
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import PHOTOS, MakeCtx
from tests.unit.test_email import RecordingSMTP
from tourgen.cli import app
from tourgen.core.pipeline import run_pipeline
from tourgen.models.delivery import NotifyRecord
from tourgen.models.job import JobOptions


@pytest.fixture
def listing_url(tmp_path: Path) -> Iterator[str]:
    site = tmp_path / "site"
    (site / "img").mkdir(parents=True)
    names = sorted(p.name for p in PHOTOS.glob("*.jpg"))
    for name in names:
        shutil.copy(PHOTOS / name, site / "img" / name)
    imgs = "".join(f'<img src="/img/{n}">' for n in names)
    (site / "listing.html").write_text(
        '<html><head><meta property="og:title" content="Test house">'
        f"</head><body>{imgs}</body></html>"
    )
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/listing.html"
    server.shutdown()


def test_cli_run_url_to_outbox(
    listing_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOURGEN_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("TOURGEN_RENDER_SCALE", "0.2")
    monkeypatch.setenv("TOURGEN_HF_POLL_INITIAL_S", "0")
    monkeypatch.setenv("TOURGEN_MUSIC_DIR", str(tmp_path / "none"))
    args = [
        "run", "--url", listing_url, "--email", "me@example.com", "--email-outbox",
        "--fake-llm", "--fake-generation", "--yes", "--job-id", "web1", "--length", "20",
    ]  # fmt: skip
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Emailed to: me@example.com" in result.output

    job = tmp_path / "jobs" / "web1"
    record = NotifyRecord.model_validate_json((job / "render" / "notify.json").read_text())
    assert record.transport == "outbox" and len(record.attachments) == 2
    eml = next((job / "render" / "email" / "outbox").glob("*.eml"))
    msg = email.message_from_bytes(eml.read_bytes())
    assert msg["To"] == "me@example.com" and "Test house" in msg["Subject"]
    body = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    assert listing_url in body.get_payload(decode=True).decode()
    assert sorted(p.get_filename() for p in msg.walk() if p.get_filename()) == [
        "web1_16x9.mp4", "web1_9x16.mp4",
    ]  # fmt: skip
    # Listing filenames survive ingest, so even the fake planner finds the rooms.
    assert "kitchen" in (job / "analysis" / "image_analyses.json").read_text()

    again = CliRunner().invoke(app, ["run", "--job-id", "web1", "--yes"])
    assert again.exit_code == 0
    assert len(list((job / "render" / "email" / "outbox").glob("*.eml"))) == 1  # not resent


def test_run_refuses_when_preflight_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOURGEN_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("TOURGEN_SMTP_HOST", raising=False)
    result = CliRunner().invoke(
        app, ["run", str(PHOTOS), "--email", "me@example.com", "--fake-llm", "--fake-generation"]
    )
    assert result.exit_code == 1
    assert "[FAIL] smtp settings" in result.output
    assert not (tmp_path / "jobs").exists()  # nothing created, nothing spent
    bad = CliRunner().invoke(app, ["doctor", "--email", "not-an-address"])
    assert bad.exit_code != 0


def test_notify_stage_over_smtp(make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", RecordingSMTP)
    ctx = make_ctx(options=JobOptions(fake_generation=True, fake_llm=True))
    ctx.settings.smtp_host, ctx.settings.smtp_user = "smtp.example.com", "bot@example.com"
    ctx.settings.smtp_password = "pw"
    ctx.job.inputs.notify_email = ["me@example.com"]
    run_pipeline(ctx)
    server = RecordingSMTP.instances[0]
    assert server.calls == ["starttls", "login:bot@example.com", "send", "quit"]
    sent = server.sent[0]
    assert sent["From"] == "bot@example.com"
    total = sum(len(p.get_payload(decode=True)) for p in sent.walk() if p.get_filename())
    assert 0 < total <= ctx.settings.email_max_attachments_mb * 1024 * 1024

    run_pipeline(ctx)
    assert len(RecordingSMTP.instances) == 1  # resume never emails twice
