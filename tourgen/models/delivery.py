"""
FILE: tourgen/models/delivery.py
ROLE: Schemas for final deliverables (stage 9): provenance manifest.

HOW IT WORKS:
  - Manifest links every clip in the final cut to its source assets, prompt,
    model, request_id and QA verdict (BUILD_PROMPT §3 step 9).

READS/WRITES: render/manifest.json

CONNECTS TO:
  - Produced by stages/deliver.py; read by api/app.py for downloads.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ManifestClip(BaseModel):
    shot_id: str
    order: int
    clip_path: str
    source_asset_ids: list[str]
    source_files: list[str]
    model_id: str | None
    prompt: str | None
    request_id: str | None
    attempts: int
    qa_verdict: str
    fidelity_score: float | None
    used_fallback: bool


class Manifest(BaseModel):
    job_id: str
    created_at: datetime
    title: str
    disclosure_text: str
    outputs: dict[str, str]  # aspect ratio -> path relative to job root
    clips: list[ManifestClip]
    total_cost_usd: float
    claude_cost_usd: float
    higgsfield_cost_usd: float
