"""
FILE: tourgen/core/preflight.py
ROLE: "Can this job actually run here?" checks, before any work or spend (`tourgen doctor`,
      and automatically at the start of `tourgen run`).

HOW IT WORKS:
  - run_checks() returns Check rows: tools (ffmpeg, font), credentials (Claude,
    Higgsfield, SMTP) and network reachability (Claude API, Higgsfield API, each
    listing URL's host, the SMTP server). Checks for anything the job won't use
    (fake modes, no URL, no email) are skipped.
  - A blocking failure means the run would fail midway; warnings don't stop it.

CONNECTS TO:
  - cli.py (doctor, run), api/app.py (job creation from URL)

INVARIANTS / GOTCHAS:
  - Network probes only open a connection / send a HEAD; nothing is billed.
  - Behind an egress proxy a denied host shows up as a connect error (403 on CONNECT).
"""

from __future__ import annotations

import shutil
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from tourgen.config import Settings
from tourgen.generation.registry import load_registry
from tourgen.models.job import JobInputs, JobOptions

ANTHROPIC_URL = "https://api.anthropic.com/"
HTTP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    def line(self) -> str:
        mark = "ok  " if self.ok else ("FAIL" if self.blocking else "warn")
        return f"[{mark}] {self.name}: {self.detail}"


def http_reachable(url: str) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=False) as client:
            response = client.head(url)
        return True, f"reachable (HTTP {response.status_code})"
    except httpx.HTTPError as exc:
        return False, f"not reachable: {type(exc).__name__}: {str(exc)[:120]}"


def tcp_reachable(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=HTTP_TIMEOUT_S):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, f"{host}:{port} not reachable: {exc}"


def run_checks(
    settings: Settings,
    inputs: JobInputs,
    options: JobOptions,
    http_probe: Callable[[str], tuple[bool, str]] = http_reachable,
    tcp_probe: Callable[[str, int], tuple[bool, str]] = tcp_reachable,
) -> list[Check]:
    checks = [
        Check("ffmpeg", bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
              "ffmpeg + ffprobe on PATH" if shutil.which("ffmpeg") else "install ffmpeg"),
        Check("font", settings.font_path.exists(), str(settings.font_path)),
    ]  # fmt: skip
    if not inputs.paths and not inputs.urls:
        checks.append(Check("inputs", False, "give photo/video paths or --url"))

    if not options.fake_llm:
        checks.append(Check("claude key", bool(settings.anthropic_api_key),
                            "ANTHROPIC_API_KEY set" if settings.anthropic_api_key
                            else "set ANTHROPIC_API_KEY in .env (or use --fake-llm)"))  # fmt: skip
        ok, detail = http_probe(ANTHROPIC_URL)
        checks.append(Check("claude network", ok, detail))

    if not options.fake_generation and not options.dry_run:
        hint = "set HF_KEY=key_id:key_secret in .env (or --fake-generation)"
        checks.append(
            Check(
                "higgsfield key", bool(settings.hf_key), "HF_KEY set" if settings.hf_key else hint
            )
        )
        ok, detail = http_probe(settings.hf_base_url)
        checks.append(Check("higgsfield network", ok, detail))
        unverified = [
            m.model_id
            for m in load_registry(settings.models_yaml).models.values()
            if not m.verified
        ]
        checks.append(Check(
            "higgsfield models", not unverified,
            "all verified" if not unverified else
            f"unverified in config/models.yaml: {', '.join(unverified)} "
            "(see docs/higgsfield-notes.md)",
            blocking=False,
        ))  # fmt: skip

    for url in inputs.urls:
        host = urlparse(url).hostname or url
        ok, detail = http_probe(url)
        checks.append(Check(f"listing {host}", ok, detail))

    if inputs.notify_email and not (options.email_outbox or settings.email_outbox_only):
        configured = bool(settings.smtp_host and (settings.smtp_from or settings.smtp_user))
        hint = "set TOURGEN_SMTP_HOST/USER/PASSWORD in .env (see .env.example)"
        where = f"{settings.smtp_host}:{settings.smtp_port}"
        checks.append(Check("smtp settings", configured, where if configured else hint))
        if configured and settings.smtp_host:
            ok, detail = tcp_probe(settings.smtp_host, settings.smtp_port)
            checks.append(Check("smtp network", ok, detail))
    return checks


def blocking_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.blocking and not c.ok]
