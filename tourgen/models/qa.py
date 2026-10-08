"""
FILE: tourgen/models/qa.py
ROLE: Schemas for clip quality control (stage 7) and per-shot outcomes.

HOW IT WORKS:
  - QAReport: structured output of prompts/qa_clip.md comparing a generated
    clip's frames against its source photo(s).
  - RetryAdjustment: what to change on the next attempt (read by compile_prompts).
  - ShotOutcome / ShotOutcomeIndex: the final decision per shot, read by assembly.

READS/WRITES: qa/<shot_id>__attempt<N>.qa.json, qa/outcomes.json

CONNECTS TO:
  - Produced by stages/qa.py; read by stages/compile_prompts.py, stages/assemble.py

INVARIANTS / GOTCHAS:
  - A report with any STRUCTURAL hallucination can never be PASS; the validator
    downgrades it to RETRY so a model mistake can't sneak a bad clip through.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Verdict(StrEnum):
    PASS = "pass"
    RETRY = "retry"
    REJECT = "reject"


class HallucinationSeverity(StrEnum):
    STRUCTURAL = "structural"  # windows, doors, walls, rooms, pools added/removed/moved
    OBJECT = "object"  # furniture or fixtures added/removed/changed
    COSMETIC = "cosmetic"  # texture/colour drift, minor smearing


class Hallucination(BaseModel):
    description: str
    severity: HallucinationSeverity
    frame_index: int | None = None


class RetryAdjustment(BaseModel):
    reduce_motion: bool = False
    shorten_duration: bool = False
    simplify_move: bool = False
    switch_model: str | None = None
    extra_preservation: list[str] = []


class QAReport(BaseModel):
    shot_id: str
    attempt: int
    fidelity_score: float = Field(ge=0.0, le=1.0)
    hallucinations: list[Hallucination] = []
    geometry_warping: bool = False
    flicker: bool = False
    artifacts: list[str] = []
    verdict: Verdict
    reasons: list[str] = []
    adjustment: RetryAdjustment = RetryAdjustment()

    @model_validator(mode="after")
    def _structural_never_passes(self) -> QAReport:
        structural = any(
            h.severity == HallucinationSeverity.STRUCTURAL for h in self.hallucinations
        )
        if structural and self.verdict == Verdict.PASS:
            self.verdict = Verdict.RETRY
            self.reasons.append("structural hallucination forces retry")
        return self


class ShotOutcome(BaseModel):
    shot_id: str
    attempts: int
    verdict: Verdict
    clip_path: str = Field(description="Clip used in the final cut, relative to job root")
    used_fallback: bool = False
    fidelity_score: float | None = None
    request_id: str | None = None
    model_id: str | None = None
    prompt: str | None = None


class ShotOutcomeIndex(BaseModel):
    outcomes: list[ShotOutcome] = []

    def get(self, shot_id: str) -> ShotOutcome | None:
        return next((o for o in self.outcomes if o.shot_id == shot_id), None)

    def upsert(self, outcome: ShotOutcome) -> None:
        self.outcomes = [o for o in self.outcomes if o.shot_id != outcome.shot_id]
        self.outcomes.append(outcome)
