"""
FILE: tourgen/stages/generate.py
ROLE: Pipeline stage 6. Budget gate, then submits attempt-1 payloads to Higgsfield
      (or the fake) through generation/scheduler.py.

HOW IT WORKS:
  1. core/costs.estimate_job() + check_budget(): over budget → BudgetExceeded;
     over confirm threshold without approval → ConfirmationRequired (CLI asks).
     Skipped for fake generation (nothing is billed).
  2. GenerationScheduler.run() on all attempt-1 payloads (resumable: existing
     records are polled/reused, never resubmitted).

READS:  plan/tour_plan.json, plan/payloads/*__attempt1.json
WRITES: clips/*__attempt1.{mp4,record.json}

CONNECTS TO:
  - Called by core/pipeline.py; stages/qa.py handles later attempts.
"""

from __future__ import annotations

from tourgen.core.context import StageContext, StageResult
from tourgen.core.costs import check_budget, estimate_job
from tourgen.generation.scheduler import GenerationScheduler
from tourgen.models.generation import GenerationPayload, GenerationRecord, RequestStatus
from tourgen.models.job import StageName
from tourgen.models.plan import TourPlan

NAME = StageName.GENERATE


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    plan = ws.read_model_or_none(ws.tour_plan_file, TourPlan)
    if plan is None:
        return False
    for shot in plan.shots:
        record = ws.read_model_or_none(ws.record_file(shot.shot_id, 1), GenerationRecord)
        if record is None or not record.status.is_terminal:
            return False
        if record.status == RequestStatus.COMPLETED and not ws.clip_file(shot.shot_id, 1).exists():
            return False
    return True


def run(ctx: StageContext) -> StageResult:
    ws = ctx.workspace
    plan = ws.read_model(ws.tour_plan_file, TourPlan)
    options = ctx.job.options
    if not options.fake_generation:
        estimate = estimate_job(plan, ctx.registry, ctx.settings, options.max_retries)
        for line in estimate.lines():
            ctx.logger.info("estimate: %s", line)
        check_budget(
            estimate, options.budget_usd, ctx.settings.confirm_above_usd, options.approved_cost_usd
        )
    payloads = [
        ws.read_model(ws.payload_file(shot.shot_id, 1), GenerationPayload) for shot in plan.shots
    ]
    scheduler = GenerationScheduler(ctx, record_costs=not options.fake_generation)
    records = scheduler.run(payloads)
    ok = sum(1 for r in records if r.status == RequestStatus.COMPLETED)
    return StageResult(f"{ok}/{len(records)} clips generated")
