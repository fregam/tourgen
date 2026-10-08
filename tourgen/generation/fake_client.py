"""
FILE: tourgen/generation/fake_client.py
ROLE: FakeHiggsfieldClient — offline double of generation/higgsfield_client.py.

HOW IT WORKS:
  - upload_file() returns "fake://<absolute path>".
  - submit() is idempotent on the key (same key → same request_id) and can
    simulate concurrency errors; get_status() completes after `polls_to_complete`.
  - download() renders a real MP4: a Ken Burns move over the first-frame image
    (or a garbage test pattern for shots listed in `bad_shots`, to exercise QA).
  - Failure modes: `fail_shots` maps a shot_id substring → "failed" | "nsfw".

CONNECTS TO:
  - Implements generation/base.VideoClient; selected by core/context.py
    (options.fake_generation) and used in tests.

INVARIANTS / GOTCHAS:
  - `label` is the shot id passed by the scheduler; fail_shots/bad_shots match on it.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tourgen.generation.base import ConcurrencyLimitError, StatusResult, SubmitResult
from tourgen.media.ffmpeg import run_ffmpeg
from tourgen.media.kenburns import make_kenburns_clip
from tourgen.models.generation import RequestStatus
from tourgen.models.plan import CameraMoveType


@dataclass
class _FakeRequest:
    request_id: str
    endpoint: str
    arguments: dict[str, Any]
    label: str
    polls: int = 0
    status: RequestStatus = RequestStatus.QUEUED


class FakeHiggsfieldClient:
    def __init__(
        self,
        work_dir: Path,
        polls_to_complete: int = 1,
        concurrency_limit: int | None = None,
        concurrency_errors: int = 0,
        fail_shots: dict[str, str] | None = None,
        bad_shots: set[str] | None = None,
        width: int = 1280,
        height: int = 720,
    ) -> None:
        self.work_dir = work_dir
        self.polls_to_complete = polls_to_complete
        self.concurrency_limit = concurrency_limit
        self.concurrency_errors = concurrency_errors
        self.fail_shots = fail_shots or {}
        self.bad_shots = bad_shots or set()
        self.width, self.height = width, height
        self.requests: dict[str, _FakeRequest] = {}
        self.submit_calls = 0
        self.uploads: list[Path] = []
        self._lock = threading.Lock()

    def upload_file(self, path: Path) -> str:
        with self._lock:
            self.uploads.append(path)
        return f"fake://{path.resolve()}"

    def submit(
        self, endpoint: str, arguments: dict[str, Any], idempotency_key: str, label: str = ""
    ) -> SubmitResult:
        with self._lock:
            self.submit_calls += 1
            if self.concurrency_errors > 0:
                self.concurrency_errors -= 1
                raise ConcurrencyLimitError("Maximum number of concurrent requests (fake)")
            request_id = "fake-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
            if request_id not in self.requests:
                active = sum(1 for r in self.requests.values() if not r.status.is_terminal)
                if self.concurrency_limit is not None and active >= self.concurrency_limit:
                    raise ConcurrencyLimitError("Maximum number of concurrent requests (fake)")
                self.requests[request_id] = _FakeRequest(
                    request_id, endpoint, arguments, label=label
                )
        return SubmitResult(
            request_id=request_id,
            status_url=f"fake://requests/{request_id}/status",
            cancel_url=f"fake://requests/{request_id}/cancel",
        )

    def get_status(self, request_id: str, status_url: str | None) -> StatusResult:
        with self._lock:
            request = self.requests[request_id]
            request.polls += 1
            for shot, outcome in self.fail_shots.items():
                if shot and shot == request.label:
                    request.status = RequestStatus(outcome)
                    return StatusResult(status=request.status, error=f"simulated {outcome}")
            if request.polls >= self.polls_to_complete:
                request.status = RequestStatus.COMPLETED
                return StatusResult(
                    status=request.status, video_url=f"fake-video://{request_id}", raw={}
                )
            request.status = RequestStatus.IN_PROGRESS
            return StatusResult(status=request.status)

    def download(self, url: str, dest: Path) -> Path:
        request = self.requests[url.removeprefix("fake-video://")]
        duration = float(request.arguments.get("duration") or 4)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if request.label in self.bad_shots:
            run_ffmpeg(
                [
                    "-f", "lavfi", "-i",
                    f"testsrc2=s={self.width}x{self.height}:r=30:d={duration}",
                    "-pix_fmt", "yuv420p", str(dest),
                ]
            )  # fmt: skip
            return dest
        image = next(
            Path(v.removeprefix("fake://"))
            for v in request.arguments.values()
            if isinstance(v, str) and v.startswith("fake://")
        )
        return make_kenburns_clip(
            image, dest, duration, CameraMoveType.DOLLY_IN, 0.3, self.width, self.height
        )
