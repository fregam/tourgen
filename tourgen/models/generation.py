"""
FILE: tourgen/models/generation.py
ROLE: Schemas for prompt compilation (stage 5) and Higgsfield generation (stage 6).

HOW IT WORKS:
  - GenerationPayload: a validated, model-specific request (endpoint + arguments).
  - GenerationRecord: everything we know about one submission; persisted the
    moment a request_id exists so a crash never loses a paid request.

READS/WRITES: plan/payloads/<shot_id>__attempt<N>.json,
              clips/<shot_id>__attempt<N>.record.json

CONNECTS TO:
  - Produced by stages/compile_prompts.py and generation/scheduler.py
  - Read by stages/qa.py, stages/deliver.py, core/costs.py

INVARIANTS / GOTCHAS:
  - `arguments` holds local asset references as "asset://<asset_id>" until the
    scheduler uploads them and swaps in public URLs (never stored back in the payload).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

ASSET_REF_PREFIX = "asset://"


class RequestStatus(StrEnum):
    PENDING = "pending"  # not yet submitted
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NSFW = "nsfw"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = {
    RequestStatus.COMPLETED,
    RequestStatus.FAILED,
    RequestStatus.NSFW,
    RequestStatus.CANCELED,
}


class GenerationPayload(BaseModel):
    shot_id: str
    attempt: int = Field(ge=1)
    model_id: str
    endpoint: str
    arguments: dict[str, Any]
    prompt: str
    duration_s: float
    image_asset_ids: list[str]
    estimated_cost_usd: float = 0.0


class GenerationRecord(BaseModel):
    shot_id: str
    attempt: int
    model_id: str
    endpoint: str
    idempotency_key: str
    request_id: str | None = None
    status: RequestStatus = RequestStatus.PENDING
    status_url: str | None = None
    cancel_url: str | None = None
    submitted_at: datetime | None = None
    finished_at: datetime | None = None
    output_url: str | None = None
    local_path: str | None = Field(default=None, description="Relative to job root")
    error: str | None = None
    cost_usd: float = 0.0
