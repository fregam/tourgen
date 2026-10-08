"""
FILE: tourgen/stages/plan.py
ROLE: Pipeline stage 4. Claude plans the tour (TourPlan); core/rules.py validates it.

HOW IT WORKS:
  1. Renders prompts/plan_tour.md with the property model, analyses, brief,
     target length and the registry's capabilities.
  2. Validates with core/rules.validate_plan(). On errors, re-asks Claude with
     the violation list (up to MAX_REPLANS times).
  3. Applies core/rules.repair_plan() for anything mechanical, re-validates,
     and fails the stage if errors remain.

READS:  analysis/*.json, assets/asset_index.json, job.json (brief, options)
WRITES: plan/tour_plan.json, plan/validation.txt

CONNECTS TO:
  - Called by core/pipeline.py; uses ctx.llm, core/rules.py, generation/registry.py
"""

from __future__ import annotations

import json

from tourgen.core.context import StageContext, StageResult
from tourgen.core.rules import RuleViolation, errors, repair_plan, validate_plan
from tourgen.core.workspace import write_text_atomic
from tourgen.models.analysis import ImageAnalysisSet, PropertyModel
from tourgen.models.assets import AssetIndex
from tourgen.models.job import StageName
from tourgen.models.plan import TourPlan

NAME = StageName.PLAN
MAX_REPLANS = 2


def is_complete(ctx: StageContext) -> bool:
    return ctx.workspace.read_model_or_none(ctx.workspace.tour_plan_file, TourPlan) is not None


def target_length_s(ctx: StageContext) -> float:
    brief = ctx.job.inputs.brief
    if brief.target_length_s:
        return brief.target_length_s
    low, high = (
        ctx.defaults.full_length_s if brief.format == "full" else ctx.defaults.social_length_s
    )
    return (low + high) / 2


def run(ctx: StageContext) -> StageResult:
    ws = ctx.workspace
    index = ws.read_model(ws.asset_index_file, AssetIndex)
    analyses = ws.read_model(ws.image_analyses_file, ImageAnalysisSet)
    property_model = ws.read_model(ws.property_model_file, PropertyModel)
    brief = ctx.job.inputs.brief
    target = target_length_s(ctx)
    variables = {
        "property_model_json": property_model.model_dump_json(),
        "analyses_json": json.dumps([a.model_dump(mode="json") for a in analyses.analyses]),
        "brief": brief.model_dump_json(exclude_none=True),
        "target_length": f"{target:g}",
        "max_shots": str(ctx.job.options.max_shots),
        "language": brief.language,
        "models_summary": ctx.registry.capabilities_summary(),
        "preferred_model": ctx.defaults.preferred_model,
        "transition_model": ctx.defaults.transition_model,
        "shot_min": f"{ctx.defaults.shot_duration_s[0]:g}",
        "shot_max": f"{ctx.defaults.shot_duration_s[1]:g}",
        "disclosure_text": ctx.defaults.disclosure_text,
        "aspect_ratios": json.dumps(brief.aspect_ratios),
        "violations": "",
    }

    violations: list[RuleViolation] = []
    plan: TourPlan | None = None
    for attempt in range(1 + MAX_REPLANS):
        plan = ctx.llm.structured(
            task=f"plan_tour:{attempt + 1}",
            model=ctx.settings.planning_model,
            prompt_name="plan_tour",
            variables=variables,
            images=[],
            schema=TourPlan,
            effort="high",
        )
        violations = validate_plan(plan, property_model, analyses, ctx.registry, index)
        if not errors(violations):
            break
        ctx.logger.info(
            "plan attempt %d has %d errors; re-asking", attempt + 1, len(errors(violations))
        )
        variables["violations"] = (
            "Your previous plan violated these rules. Fix every one:\n"
            + "\n".join(f"- {v}" for v in errors(violations))
            + "\nPrevious plan:\n"
            + plan.model_dump_json()
        )
    assert plan is not None

    plan = repair_plan(
        plan, property_model, analyses, ctx.registry, index,
        ctx.defaults.preferred_model, ctx.defaults.disclosure_text,
    )  # fmt: skip
    plan.shots = plan.shots[: ctx.job.options.max_shots]
    plan.aspect_ratios = brief.aspect_ratios or plan.aspect_ratios
    violations = validate_plan(plan, property_model, analyses, ctx.registry, index)
    report = "\n".join(str(v) for v in violations) or "OK: no violations"
    write_text_atomic(ws.plan_dir / "validation.txt", report + "\n")
    if errors(violations):
        raise ValueError(f"Tour plan still invalid after repair:\n{report}")
    ws.write_model(ws.tour_plan_file, plan)
    return StageResult(
        f"{len(plan.shots)} shots, {plan.total_duration_s:.0f}s planned "
        f"(target {target:.0f}s), {len(violations)} warnings"
    )
