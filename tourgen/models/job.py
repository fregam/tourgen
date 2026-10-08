"""
FILE: tourgen/models/job.py
ROLE: Job-level state: inputs, brief, options, stage statuses, cost ledger.

HOW IT WORKS:
  - JobState is persisted at jobs/<job_id>/job.json and is the single source
    of truth for resume/skip decisions in core/pipeline.py.

READS/WRITES: job.json (via core/workspace.py)

CONNECTS TO:
  - core/pipeline.py, core/costs.py, cli.py, api/app.py

INVARIANTS / GOTCHAS:
  - Stage order is defined by STAGE_ORDER; keep it in sync with BUILD_PROMPT §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StageName(StrEnum):
    INGEST = "ingest"
    NORMALIZE = "normalize"
    UNDERSTAND = "understand"
    PLAN = "plan"
    COMPILE = "compile"
    GENERATE = "generate"
    QA = "qa"
    ASSEMBLE = "assemble"
    DELIVER = "deliver"


STAGE_ORDER: list[StageName] = list(StageName)


class StageState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StageStatus(BaseModel):
    state: StageState = StageState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str | None = None
    error: str | None = None


class Brief(BaseModel):
    text: str = ""
    audience: str | None = None
    tone: str | None = None
    language: str = "en"
    target_length_s: float | None = None
    format: str = Field(default="social", description="social | full")
    aspect_ratios: list[str] = ["16:9", "9:16"]
    agent_name: str | None = None
    agent_contact: str | None = None
    hero_first: bool = False


class JobInputs(BaseModel):
    paths: list[str] = []
    urls: list[str] = []
    floor_plan: str | None = None
    brief: Brief = Brief()


class JobOptions(BaseModel):
    fake_generation: bool = False
    fake_llm: bool = False
    dry_run: bool = False
    max_shots: int = 30
    max_retries: int = 2
    budget_usd: float = 25.0
    approved_cost_usd: float | None = None


class CostEntry(BaseModel):
    provider: str  # "claude" | "higgsfield"
    description: str
    usd: float
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CostLedger(BaseModel):
    entries: list[CostEntry] = []

    def total(self, provider: str | None = None) -> float:
        return sum(e.usd for e in self.entries if provider is None or e.provider == provider)


class JobState(BaseModel):
    job_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    inputs: JobInputs = JobInputs()
    options: JobOptions = JobOptions()
    stages: dict[StageName, StageStatus] = Field(
        default_factory=lambda: {name: StageStatus() for name in STAGE_ORDER}
    )
    ledger: CostLedger = CostLedger()
    errors: list[str] = []

    def stage(self, name: StageName) -> StageStatus:
        return self.stages.setdefault(name, StageStatus())
