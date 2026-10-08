"""
FILE: tourgen/core/costs.py
ROLE: Cost/time estimation before generation (`tourgen estimate`) and budget gating.

HOW IT WORKS:
  - estimate_job() sums registry usd_per_second × clamped shot durations for the
    expected case, and × (1 + max_retries) for the worst case; adds a rough Claude
    estimate for QA (+ remaining planning work) from token heuristics.
  - check_budget() raises BudgetExceeded / ConfirmationRequired.

CONNECTS TO:
  - cli.py (estimate, generate), stages/generate.py, api/app.py

INVARIANTS / GOTCHAS:
  - Higgsfield prices in config/models.yaml are placeholders until verified;
    the report says so. Actual spend is in JobState.ledger.
"""

from __future__ import annotations

from dataclasses import dataclass

from tourgen.config import Settings
from tourgen.generation.registry import ModelRegistry
from tourgen.models.plan import TourPlan

# Rough per-call token heuristics for Claude QA (≈7 images + text in, ~800 tokens out).
QA_INPUT_TOKENS = 12_000
QA_OUTPUT_TOKENS = 1_500
SECONDS_PER_CLIP = 120.0  # typical Higgsfield turnaround per clip, used for time estimates


class BudgetExceeded(RuntimeError):
    pass


class ConfirmationRequired(RuntimeError):
    def __init__(self, estimate_usd: float) -> None:
        super().__init__(f"Estimated cost ${estimate_usd:.2f} needs confirmation (--yes)")
        self.estimate_usd = estimate_usd


@dataclass(frozen=True)
class CostEstimate:
    shots: int
    video_seconds: float
    higgsfield_usd: float
    higgsfield_worst_usd: float
    claude_usd: float
    est_minutes: float
    verified_prices: bool

    @property
    def total_usd(self) -> float:
        return self.higgsfield_usd + self.claude_usd

    @property
    def worst_total_usd(self) -> float:
        return self.higgsfield_worst_usd + self.claude_usd * 2

    def lines(self) -> list[str]:
        note = "" if self.verified_prices else " (UNVERIFIED placeholder prices)"
        return [
            f"Shots: {self.shots}, generated video: {self.video_seconds:.0f}s",
            f"Higgsfield: ${self.higgsfield_usd:.2f} expected, "
            f"${self.higgsfield_worst_usd:.2f} worst case{note}",
            f"Claude (QA): ~${self.claude_usd:.2f}",
            f"Total: ~${self.total_usd:.2f} (worst ${self.worst_total_usd:.2f}), "
            f"~{self.est_minutes:.0f} min",
        ]


def estimate_job(
    plan: TourPlan, registry: ModelRegistry, settings: Settings, max_retries: int
) -> CostEstimate:
    seconds = 0.0
    hf = 0.0
    verified = True
    for shot in plan.shots:
        spec = registry.get(shot.model_id)
        duration = spec.clamp_duration(shot.duration_s)
        seconds += duration
        hf += duration * spec.usd_per_second
        verified = verified and spec.verified
    price = settings.claude_prices.get(settings.qa_model)
    qa_call = 0.0
    if price is not None:
        qa_call = (
            QA_INPUT_TOKENS * price.input_per_mtok + QA_OUTPUT_TOKENS * price.output_per_mtok
        ) / 1_000_000
    claude = qa_call * len(plan.shots)
    waves = -(-len(plan.shots) // max(settings.hf_concurrency, 1))
    return CostEstimate(
        shots=len(plan.shots),
        video_seconds=seconds,
        higgsfield_usd=round(hf, 2),
        higgsfield_worst_usd=round(hf * (1 + max_retries), 2),
        claude_usd=round(claude, 2),
        est_minutes=waves * SECONDS_PER_CLIP / 60,
        verified_prices=verified,
    )


def check_budget(
    estimate: CostEstimate, budget_usd: float, confirm_above_usd: float, approved_usd: float | None
) -> None:
    if estimate.total_usd > budget_usd:
        raise BudgetExceeded(
            f"Estimated ${estimate.total_usd:.2f} exceeds budget ${budget_usd:.2f} (--budget)"
        )
    if estimate.total_usd > confirm_above_usd and (
        approved_usd is None or approved_usd < estimate.total_usd
    ):
        raise ConfirmationRequired(estimate.total_usd)
