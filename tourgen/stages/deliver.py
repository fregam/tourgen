"""
FILE: tourgen/stages/deliver.py
ROLE: Pipeline stage 9. Writes provenance (manifest.json) and a human report (report.md).

HOW IT WORKS:
  - Manifest: every clip in the final cut ↔ source assets ↔ prompt ↔ model ↔
    request_id ↔ QA verdict, plus output paths and costs.
  - Report: outputs, cost (estimate vs actual), per-stage timings, QA summary.

READS:  job.json, plan/tour_plan.json, qa/outcomes.json, assets/asset_index.json
WRITES: render/manifest.json, render/report.md

CONNECTS TO:
  - Called by core/pipeline.py; manifest is served by api/app.py
"""

from __future__ import annotations

from datetime import UTC, datetime

from tourgen.core.context import StageContext, StageResult
from tourgen.core.costs import estimate_job
from tourgen.core.workspace import write_text_atomic
from tourgen.models.assets import AssetIndex
from tourgen.models.delivery import Manifest, ManifestClip
from tourgen.models.job import StageName
from tourgen.models.plan import TourPlan
from tourgen.models.qa import ShotOutcomeIndex

NAME = StageName.DELIVER


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    return ws.manifest_file.exists() and ws.report_file.exists()


def run(ctx: StageContext) -> StageResult:
    ws = ctx.workspace
    plan = ws.read_model(ws.tour_plan_file, TourPlan)
    outcomes = ws.read_model(ws.outcomes_file, ShotOutcomeIndex)
    index = ws.read_model(ws.asset_index_file, AssetIndex)
    ledger = ctx.job.ledger
    clips = []
    for order, shot in enumerate(plan.shots, start=1):
        outcome = outcomes.get(shot.shot_id)
        assert outcome is not None
        clips.append(
            ManifestClip(
                shot_id=shot.shot_id,
                order=order,
                clip_path=outcome.clip_path,
                source_asset_ids=shot.source_asset_ids,
                source_files=[index.get(a).source for a in shot.source_asset_ids],
                model_id=outcome.model_id,
                prompt=outcome.prompt,
                request_id=outcome.request_id,
                attempts=outcome.attempts,
                qa_verdict=outcome.verdict.value,
                fidelity_score=outcome.fidelity_score,
                used_fallback=outcome.used_fallback,
            )
        )
    manifest = Manifest(
        job_id=ws.job_id,
        created_at=datetime.now(UTC),
        title=plan.title,
        disclosure_text=plan.disclosure_text,
        outputs={a: ws.rel(ws.final_render_file(a)) for a in plan.aspect_ratios},
        clips=clips,
        total_cost_usd=round(ledger.total(), 4),
        claude_cost_usd=round(ledger.total("claude"), 4),
        higgsfield_cost_usd=round(ledger.total("higgsfield"), 4),
    )
    ws.write_model(ws.manifest_file, manifest)
    write_text_atomic(ws.report_file, build_report(ctx, plan, manifest))
    return StageResult(
        f"manifest with {len(clips)} clips; total cost ${manifest.total_cost_usd:.2f}"
    )


def build_report(ctx: StageContext, plan: TourPlan, manifest: Manifest) -> str:
    estimate = estimate_job(plan, ctx.registry, ctx.settings, ctx.job.options.max_retries)
    lines = [
        f"# Tour report — {manifest.title}",
        "",
        f"Job `{manifest.job_id}` · generated {manifest.created_at:%Y-%m-%d %H:%M} UTC",
        f"Disclosure: _{manifest.disclosure_text}_",
        "",
        "## Outputs",
        *[f"- {aspect}: `{path}`" for aspect, path in manifest.outputs.items()],
        "",
        "## Cost",
        f"- Actual: ${manifest.total_cost_usd:.2f} (Claude ${manifest.claude_cost_usd:.2f}, "
        f"Higgsfield ${manifest.higgsfield_cost_usd:.2f})",
        f"- Estimate before generation: ~${estimate.total_usd:.2f} "
        f"(worst ${estimate.worst_total_usd:.2f})",
    ]
    if ctx.job.options.fake_generation:
        lines.append("- Fake generation: Higgsfield was not called.")
    lines += ["", "## Stage timings"]
    for name, status in ctx.job.stages.items():
        if status.started_at and status.finished_at:
            seconds = (status.finished_at - status.started_at).total_seconds()
            lines.append(f"- {name}: {seconds:.1f}s — {status.summary or ''}")
    lines += [
        "",
        "## QA",
        "| # | Shot | Model | Attempts | Verdict | Fidelity | Fallback |",
        "|---|---|---|---|---|---|---|",
    ]
    for clip in manifest.clips:
        fidelity = f"{clip.fidelity_score:.2f}" if clip.fidelity_score is not None else "-"
        lines.append(
            f"| {clip.order} | {clip.shot_id} | {clip.model_id} | {clip.attempts} | "
            f"{clip.qa_verdict} | {fidelity} | {'yes' if clip.used_fallback else 'no'} |"
        )
    return "\n".join(lines) + "\n"
