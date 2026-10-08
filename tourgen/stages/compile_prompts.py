"""
FILE: tourgen/stages/compile_prompts.py
ROLE: Pipeline stage 5. Turns each Shot into a validated, model-specific GenerationPayload.

HOW IT WORKS:
  - compile_shot() picks the builder for the shot's model family
    (generation/prompt_builders/) and writes plan/payloads/<shot>__attemptN.json.
  - run() compiles attempt 1 for every shot. Retries (attempt ≥ 2) are compiled
    by stages/qa.py through compile_shot() with an adjusted Shot.

READS:  plan/tour_plan.json, analysis/image_analyses.json
WRITES: plan/payloads/<shot_id>__attempt<N>.json

CONNECTS TO:
  - Called by core/pipeline.py and stages/qa.py; uses generation/registry.py

INVARIANTS / GOTCHAS:
  - Existing payload files are reused, never overwritten: what was (or may have
    been) submitted must stay on disk exactly as sent.
"""

from __future__ import annotations

from tourgen.core.context import StageContext, StageResult
from tourgen.generation.prompt_builders import builder_for
from tourgen.models.analysis import ImageAnalysisSet
from tourgen.models.generation import GenerationPayload
from tourgen.models.job import StageName
from tourgen.models.plan import Shot, TourPlan

NAME = StageName.COMPILE


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    plan = ws.read_model_or_none(ws.tour_plan_file, TourPlan)
    return plan is not None and all(ws.payload_file(s.shot_id, 1).exists() for s in plan.shots)


def compile_shot(
    ctx: StageContext,
    shot: Shot,
    attempt: int,
    extra_preservation: list[str] | None = None,
) -> GenerationPayload:
    ws = ctx.workspace
    path = ws.payload_file(shot.shot_id, attempt)
    existing = ws.read_model_or_none(path, GenerationPayload)
    if existing is not None:
        return existing
    analyses = ws.read_model(ws.image_analyses_file, ImageAnalysisSet)
    try:
        analysis = analyses.get(shot.source_asset_ids[0])
    except KeyError:
        analysis = None
    spec = ctx.registry.get(shot.model_id)
    payload = builder_for(spec)(shot, spec, attempt, analysis, extra_preservation or [])
    ws.write_model(path, payload)
    return payload


def run(ctx: StageContext) -> StageResult:
    plan = ctx.workspace.read_model(ctx.workspace.tour_plan_file, TourPlan)
    payloads = [compile_shot(ctx, shot, 1) for shot in plan.shots]
    total = sum(p.estimated_cost_usd for p in payloads)
    return StageResult(f"{len(payloads)} payloads compiled (~${total:.2f} at registry prices)")
