"""
FILE: tourgen/generation/scheduler.py
ROLE: Runs GenerationPayloads against a VideoClient: bounded concurrency, idempotent
      submission, persisted request ids, polling with backoff + jitter, immediate download.

HOW IT WORKS (per payload, in a worker thread; a BoundedSemaphore caps in-flight requests):
  1. Load clips/<shot>__attemptN.record.json if present:
       - completed + local clip on disk → nothing to do (no duplicate charge);
       - has request_id and not terminal → resume polling, never resubmit.
  2. Otherwise upload "asset://" images, submit with Idempotency-Key =
     sha256(job:shot:attempt), and persist the record IMMEDIATELY.
  3. Poll until terminal (backoff + jitter), download the MP4 at once, record cost.
  Concurrency/transient errors back off and retry with the SAME key.

READS:  plan/payloads/*.json (via caller), assets/
WRITES: clips/<shot>__attemptN.record.json, clips/<shot>__attemptN.mp4

CONNECTS TO:
  - stages/generate.py, stages/qa.py (retries); generation/base.VideoClient

INVARIANTS / GOTCHAS:
  - Records are written under a lock; each payload owns its own record file.
"""

from __future__ import annotations

import hashlib
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from tourgen.core.context import StageContext
from tourgen.generation.base import GenerationError, TransientError
from tourgen.models.assets import AssetIndex
from tourgen.models.generation import (
    ASSET_REF_PREFIX,
    GenerationPayload,
    GenerationRecord,
    RequestStatus,
)

MAX_SUBMIT_TRIES = 8
MAX_POLL_ERRORS = 5


def idempotency_key(job_id: str, shot_id: str, attempt: int) -> str:
    return hashlib.sha256(f"{job_id}:{shot_id}:{attempt}".encode()).hexdigest()[:40]


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter."""
    if base <= 0:
        return 0.0
    return float(random.uniform(0, min(cap, base * 2 ** (attempt - 1))))


class GenerationScheduler:
    def __init__(
        self,
        ctx: StageContext,
        sleep: Callable[[float], None] = time.sleep,
        record_costs: bool = True,
    ) -> None:
        self.ctx = ctx
        self.sleep = sleep
        self.record_costs = record_costs  # False for fake generation (nothing is billed)
        self.semaphore = threading.BoundedSemaphore(max(ctx.settings.hf_concurrency, 1))
        self._upload_cache: dict[str, str] = {}
        self._asset_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self.index = ctx.workspace.read_model(ctx.workspace.asset_index_file, AssetIndex)

    def run(self, payloads: list[GenerationPayload]) -> list[GenerationRecord]:
        workers = max(self.ctx.settings.hf_concurrency, 1)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.process, payloads))

    # --- one payload ----------------------------------------------------------
    def process(self, payload: GenerationPayload) -> GenerationRecord:
        ws = self.ctx.workspace
        record_path = ws.record_file(payload.shot_id, payload.attempt)
        clip_path = ws.clip_file(payload.shot_id, payload.attempt)
        record = ws.read_model_or_none(record_path, GenerationRecord)
        if record and record.status == RequestStatus.COMPLETED and clip_path.exists():
            return record
        if record is None or record.request_id is None:
            record = GenerationRecord(
                shot_id=payload.shot_id,
                attempt=payload.attempt,
                model_id=payload.model_id,
                endpoint=payload.endpoint,
                idempotency_key=idempotency_key(ws.job_id, payload.shot_id, payload.attempt),
            )
        with self.semaphore:
            try:
                if record.request_id is None:
                    self._submit(payload, record)
                if not record.status.is_terminal:
                    self._poll(record)
                if record.status == RequestStatus.COMPLETED and not clip_path.exists():
                    self._download(payload, record)
            except GenerationError as exc:
                record.error = str(exc)
                if record.request_id is None:
                    record.status = RequestStatus.FAILED
                self._save(record)
                self.ctx.logger.warning(
                    "shot %s attempt %s: %s", payload.shot_id, payload.attempt, exc
                )
        return record

    def _submit(self, payload: GenerationPayload, record: GenerationRecord) -> None:
        arguments = self._resolve_assets(payload.arguments)
        for attempt in range(1, MAX_SUBMIT_TRIES + 1):
            try:
                result = self.ctx.video.submit(
                    payload.endpoint, arguments, record.idempotency_key, label=payload.shot_id
                )
                break
            except TransientError as exc:
                if attempt == MAX_SUBMIT_TRIES:
                    raise
                delay = backoff_delay(attempt, self.ctx.settings.hf_poll_initial_s, 60.0)
                self.ctx.logger.info("submit %s retry in %.1fs: %s", payload.shot_id, delay, exc)
                self.sleep(delay)
        record.request_id = result.request_id
        record.status_url = result.status_url
        record.cancel_url = result.cancel_url
        record.status = RequestStatus.QUEUED
        record.submitted_at = datetime.now(UTC)
        self._save(record)  # persist the paid request id before anything else can fail

    def _poll(self, record: GenerationRecord) -> None:
        assert record.request_id is not None
        settings = self.ctx.settings
        deadline = time.monotonic() + settings.hf_timeout_s
        polls, errors = 0, 0
        while time.monotonic() < deadline:
            polls += 1
            try:
                status = self.ctx.video.get_status(record.request_id, record.status_url)
            except TransientError:
                errors += 1
                if errors > MAX_POLL_ERRORS:
                    raise
                self.sleep(
                    backoff_delay(errors, settings.hf_poll_initial_s, settings.hf_poll_max_s)
                )
                continue
            if status.status != record.status:
                record.status = status.status
                self._save(record)
            if status.status.is_terminal:
                record.output_url = status.video_url
                record.error = status.error
                record.finished_at = datetime.now(UTC)
                self._save(record)
                return
            delay = min(settings.hf_poll_max_s, settings.hf_poll_initial_s * 1.5 ** min(polls, 8))
            self.sleep(delay * random.uniform(0.8, 1.2))
        raise GenerationError(f"timed out waiting for {record.request_id}")

    def _download(self, payload: GenerationPayload, record: GenerationRecord) -> None:
        if not record.output_url:
            raise GenerationError(f"{record.request_id} completed without a video URL")
        clip_path = self.ctx.workspace.clip_file(payload.shot_id, payload.attempt)
        self.ctx.video.download(record.output_url, clip_path)
        record.local_path = self.ctx.workspace.rel(clip_path)
        record.cost_usd = payload.estimated_cost_usd if self.record_costs else 0.0
        self._save(record)
        if not self.record_costs:
            return
        self.ctx.record_cost(
            "higgsfield", f"{payload.shot_id} attempt {payload.attempt} ({payload.model_id})",
            payload.estimated_cost_usd,
        )  # fmt: skip

    def _resolve_assets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.startswith(ASSET_REF_PREFIX):
                resolved[key] = self._upload(value.removeprefix(ASSET_REF_PREFIX))
            else:
                resolved[key] = value
        return resolved

    def _upload(self, asset_id: str) -> str:
        # Per-asset lock: shots sharing an image (transitions, hero reuse) upload it once.
        with self._lock:
            asset_lock = self._asset_locks.setdefault(asset_id, threading.Lock())
        with asset_lock:
            if asset_id in self._upload_cache:
                return self._upload_cache[asset_id]
            asset = self.index.get(asset_id)
            if asset.normalized_path is None:
                raise GenerationError(f"asset {asset_id} has no normalized image")
            url = self.ctx.video.upload_file(self.ctx.workspace.abs(asset.normalized_path))
            self._upload_cache[asset_id] = url
            return url

    def _save(self, record: GenerationRecord) -> None:
        with self._lock:
            path = self.ctx.workspace.record_file(record.shot_id, record.attempt)
            self.ctx.workspace.write_model(path, record)
