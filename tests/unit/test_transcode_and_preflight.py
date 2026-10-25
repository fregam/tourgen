"""media/transcode.py size fitting and core/preflight.py checks."""

from __future__ import annotations

from pathlib import Path

from tourgen.config import Settings
from tourgen.core.preflight import blocking_failures, run_checks
from tourgen.media.ffmpeg import probe, run_ffmpeg
from tourgen.media.transcode import fit_to_size
from tourgen.models.job import JobInputs, JobOptions


def _video(path: Path, seconds: int = 6) -> Path:
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=d={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ])  # fmt: skip
    return path


def test_fit_to_size(tmp_path: Path) -> None:
    src = _video(tmp_path / "big.mp4")
    size = src.stat().st_size
    copy = fit_to_size(src, tmp_path / "copy.mp4", size + 10)
    assert copy is not None and copy.stat().st_size == size

    budget = size // 4
    small = fit_to_size(src, tmp_path / "small.mp4", budget)
    assert small is not None and small.stat().st_size <= budget
    info = probe(small)
    assert info.has_audio and abs(info.duration_s - 6) < 0.3

    assert fit_to_size(src, tmp_path / "tiny.mp4", 20_000) is None  # unwatchable → skip


def _probe_ok(url: str) -> tuple[bool, str]:
    return True, "reachable"


def _probe_blocked(url: str) -> tuple[bool, str]:
    return False, "not reachable: ConnectError"


def test_preflight_reports_what_is_missing() -> None:
    inputs = JobInputs(urls=["https://listing.example/1"], notify_email=["me@example.com"])
    checks = run_checks(
        Settings(anthropic_api_key=None, hf_key=None, smtp_host=None),
        inputs, JobOptions(), http_probe=_probe_blocked, tcp_probe=lambda h, p: (False, "no"),
    )  # fmt: skip
    failing = {c.name for c in blocking_failures(checks)}
    assert {"claude key", "claude network", "higgsfield key", "higgsfield network",
            "listing listing.example", "smtp settings"} <= failing  # fmt: skip
    assert any(c.name == "higgsfield models" and not c.blocking for c in checks)


def test_preflight_fake_modes_and_outbox_skip_external_checks() -> None:
    inputs = JobInputs(paths=["/photos"], notify_email=["me@example.com"])
    options = JobOptions(fake_llm=True, fake_generation=True, email_outbox=True)
    checks = run_checks(Settings(), inputs, options, http_probe=_probe_blocked)
    assert blocking_failures(checks) == []
    assert {c.name for c in checks} == {"ffmpeg", "font"}


def test_preflight_smtp_network_probe() -> None:
    settings = Settings(anthropic_api_key="k", hf_key="a:b", smtp_host="smtp.x.com",
                        smtp_user="me@x.com")  # fmt: skip
    inputs = JobInputs(paths=["/p"], notify_email=["me@example.com"])
    checks = run_checks(settings, inputs, JobOptions(), http_probe=_probe_ok,
                        tcp_probe=lambda h, p: (False, f"{h}:{p} blocked"))  # fmt: skip
    assert [c.name for c in blocking_failures(checks)] == ["smtp network"]
