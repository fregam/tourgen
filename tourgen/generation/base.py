"""
FILE: tourgen/generation/base.py
ROLE: The video-generation client interface + its error types.

HOW IT WORKS:
  - VideoClient is implemented by generation/higgsfield_client.py (real) and
    generation/fake_client.py (tests/dev). The scheduler only talks to this.

CONNECTS TO:
  - generation/scheduler.py, core/context.py

INVARIANTS / GOTCHAS:
  - ConcurrencyLimitError and TransientError are retryable with backoff;
    GenerationError is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tourgen.models.generation import RequestStatus


class GenerationError(RuntimeError):
    pass


class TransientError(GenerationError):
    """Network error, 429 or 5xx: safe to retry (with the same Idempotency-Key)."""


class ConcurrencyLimitError(TransientError):
    """Account concurrency cap hit (Higgsfield returns HTTP 400, no Retry-After)."""


@dataclass(frozen=True)
class SubmitResult:
    request_id: str
    status_url: str
    cancel_url: str | None = None


@dataclass(frozen=True)
class StatusResult:
    status: RequestStatus
    video_url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VideoClient(Protocol):
    def upload_file(self, path: Path) -> str: ...

    def submit(
        self, endpoint: str, arguments: dict[str, Any], idempotency_key: str, label: str = ""
    ) -> SubmitResult: ...

    def get_status(self, request_id: str, status_url: str | None) -> StatusResult: ...

    def download(self, url: str, dest: Path) -> Path: ...
