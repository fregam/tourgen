"""
FILE: tourgen/stages/qa.py
ROLE: Pipeline stage 7. Checks each generated clip against its source photo(s) and decides
      pass / retry / reject; drives retries and the Ken Burns fallback.

HOW IT WORKS (per shot, shots in parallel; generation concurrency is capped by the scheduler):
  1. For attempt N: ensure the clip exists (compile + generate attempts ≥ 2 on demand).
  2. Extract frames (media/frames.py) → Claude with prompts/qa_clip.md → QAReport.
     A failed/NSFW generation gets a synthetic RETRY report (simplify the move).
  3. PASS → done. RETRY with attempts left → apply the report's RetryAdjustment
     (generation/prompt_builders/base.py) and loop. REJECT or out of attempts →
     media/kenburns.py fallback (zero hallucination risk).
  4. Writes the ShotOutcome; assembly uses only outcomes.

READS:  clips/<shot_id>__attempt<N>.mp4, plan/tour_plan.json, assets/
WRITES: qa/<shot_id>__attempt<N>.qa.json, qa/frames/, qa/outcomes.json,
        clips/<shot_id>__kenburns.mp4 (fallback)

CONNECTS TO:
  - Called by core/pipeline.py
  - Uses media/frames.py, media/kenburns.py, ctx.llm, generation/scheduler.py,
    stages/compile_prompts.compile_shot

INVARIANTS / GOTCHAS:
  - A clip with any "structural" hallucination is never "pass" (QAReport validator).
  - Never deletes clips; failed attempts stay on disk for debugging.
  - Cached QA reports make the retry chain deterministic on resume.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tourgen.core.context import StageContext, StageResult
from tourgen.generation.prompt_builders.base import apply_retry_adjustment
from tourgen.generation.scheduler import GenerationScheduler
from tourgen.llm.base import ImageInput
from tourgen.media.frames import extract_frames
from tourgen.media.kenburns import make_kenburns_clip
from tourgen.models.analysis import ImageAnalysis, ImageAnalysisSet
from tourgen.models.assets import AssetIndex
from tourgen.models.generation import GenerationRecord, RequestStatus
from tourgen.models.job import StageName
from tourgen.models.plan import Shot, TourPlan
from tourgen.models.qa import QAReport, RetryAdjustment, ShotOutcome, ShotOutcomeIndex, Verdict
from tourgen.stages.compile_prompts import compile_shot

NAME = StageName.QA
QA_FRAME_COUNT = 6


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    plan = ws.read_model_or_none(ws.tour_plan_file, TourPlan)
    outcomes = ws.read_model_or_none(ws.outcomes_file, ShotOutcomeIndex)
    if plan is None or outcomes is None:
        return False
    for shot in plan.shots:
        outcome = outcomes.get(shot.shot_id)
        if outcome is None or not ws.abs(outcome.clip_path).exists():
            return False
    return True


class _QARunner:
    def __init__(self, ctx: StageContext) -> None:
        self.ctx = ctx
        ws = ctx.workspace
        self.index = ws.read_model(ws.asset_index_file, AssetIndex)
        self.analyses = ws.read_model(ws.image_analyses_file, ImageAnalysisSet)
        self.outcomes = (
            ws.read_model_or_none(ws.outcomes_file, ShotOutcomeIndex) or ShotOutcomeIndex()
        )
        self.scheduler = GenerationScheduler(ctx, record_costs=not ctx.job.options.fake_generation)
        self.max_attempts = 1 + ctx.job.options.max_retries
        self.lock = threading.Lock()

    def run_shot(self, plan_shot: Shot) -> ShotOutcome:
        existing = self.outcomes.get(plan_shot.shot_id)
        if existing is not None and self.ctx.workspace.abs(existing.clip_path).exists():
            return existing
        shot = plan_shot
        attempt = 1
        report: QAReport | None = None
        while True:
            payload = compile_shot(self.ctx, shot, attempt, self._preservation(report))
            record = self.scheduler.process(payload)
            report = self._report(shot, attempt, record)
            if report.verdict == Verdict.PASS:
                outcome = ShotOutcome(
                    shot_id=shot.shot_id,
                    attempts=attempt,
                    verdict=Verdict.PASS,
                    clip_path=self.ctx.workspace.rel(
                        self.ctx.workspace.clip_file(shot.shot_id, attempt)
                    ),
                    fidelity_score=report.fidelity_score,
                    request_id=record.request_id,
                    model_id=payload.model_id,
                    prompt=payload.prompt,
                )
                break
            if report.verdict == Verdict.REJECT or attempt >= self.max_attempts:
                outcome = self._fallback(plan_shot, attempt, report)
                break
            shot = apply_retry_adjustment(
                shot, report.adjustment, self.ctx.registry, self._analysis(shot)
            )
            attempt += 1
        with self.lock:
            self.outcomes.upsert(outcome)
            self.ctx.workspace.write_model(self.ctx.workspace.outcomes_file, self.outcomes)
        return outcome

    def _report(self, shot: Shot, attempt: int, record: GenerationRecord) -> QAReport:
        ws = self.ctx.workspace
        path = ws.qa_report_file(shot.shot_id, attempt)
        cached = ws.read_model_or_none(path, QAReport)
        if cached is not None:
            return cached
        clip = ws.clip_file(shot.shot_id, attempt)
        if record.status != RequestStatus.COMPLETED or not clip.exists():
            report = QAReport(
                shot_id=shot.shot_id,
                attempt=attempt,
                fidelity_score=0.0,
                verdict=Verdict.RETRY,
                reasons=[f"generation {record.status}: {record.error or 'no clip'}"],
                adjustment=RetryAdjustment(simplify_move=True, reduce_motion=True),
            )
        else:
            frames = extract_frames(clip, ws.qa_frames_for(shot.shot_id, attempt), QA_FRAME_COUNT)
            images = [
                ImageInput(f"SOURCE {n + 1} (asset_id={aid})", self._source_path(aid))
                for n, aid in enumerate(shot.source_asset_ids)
            ] + [ImageInput(f"FRAME {n}", f) for n, f in enumerate(frames)]
            report = self.ctx.llm.structured(
                task=f"qa_clip:{shot.shot_id}:{attempt}",
                model=self.ctx.settings.qa_model,
                prompt_name="qa_clip",
                variables={
                    "shot_json": shot.model_dump_json(),
                    "shot_id": shot.shot_id,
                    "attempt": str(attempt),
                    "max_attempts": str(self.max_attempts),
                    "model_ids": ", ".join(self.ctx.registry.models),
                },
                images=images,
                schema=QAReport,
                effort="medium",
            )
            report.shot_id, report.attempt = shot.shot_id, attempt
            report = QAReport.model_validate(report.model_dump())  # re-run validators
        ws.write_model(path, report)
        return report

    def _fallback(self, shot: Shot, attempts: int, report: QAReport) -> ShotOutcome:
        ws = self.ctx.workspace
        out = ws.kenburns_file(shot.shot_id)
        if not out.exists():
            make_kenburns_clip(
                self._source_path(shot.source_asset_ids[0]),
                out,
                shot.duration_s,
                shot.camera_move.type,
                shot.camera_move.strength,
                width=int(1920 * self.ctx.settings.render_scale) // 2 * 2,
                height=int(1080 * self.ctx.settings.render_scale) // 2 * 2,
                fps=self.ctx.defaults.fps,
            )
        self.ctx.logger.info(
            "shot %s: Ken Burns fallback after %d attempt(s)", shot.shot_id, attempts
        )
        return ShotOutcome(
            shot_id=shot.shot_id,
            attempts=attempts,
            verdict=report.verdict,
            clip_path=ws.rel(out),
            used_fallback=True,
            fidelity_score=1.0,
            model_id="local-kenburns",
        )

    def _source_path(self, asset_id: str) -> Path:
        asset = self.index.get(asset_id)
        assert asset.normalized_path is not None
        return self.ctx.workspace.abs(asset.normalized_path)

    def _analysis(self, shot: Shot) -> ImageAnalysis | None:
        try:
            return self.analyses.get(shot.source_asset_ids[0])
        except KeyError:
            return None

    @staticmethod
    def _preservation(report: QAReport | None) -> list[str]:
        return list(report.adjustment.extra_preservation) if report else []


def run(ctx: StageContext) -> StageResult:
    plan = ctx.workspace.read_model(ctx.workspace.tour_plan_file, TourPlan)
    runner = _QARunner(ctx)
    with ThreadPoolExecutor(max_workers=max(ctx.settings.hf_concurrency, 1)) as pool:
        outcomes = list(pool.map(runner.run_shot, plan.shots))
    passed = sum(1 for o in outcomes if not o.used_fallback)
    retried = sum(1 for o in outcomes if o.attempts > 1)
    fallback = sum(1 for o in outcomes if o.used_fallback)
    return StageResult(f"{passed} passed, {retried} needed retries, {fallback} Ken Burns fallbacks")
