"""
FILE: tourgen/core/pipeline.py
ROLE: Orchestrator. Runs stages in order, skips completed ones, records status in job.json.

HOW IT WORKS:
  1. create_job() makes the workspace and the initial JobState.
  2. run_pipeline() walks STAGE_ORDER. A stage is skipped when job.json says
     DONE *and* the stage's is_complete(ctx) confirms its outputs are valid.
  3. When a stage actually runs, every later stage is reset to PENDING so
     stale downstream outputs are rebuilt (they still skip work they can reuse,
     e.g. generate never resubmits a shot whose record already exists).
  4. dry_run stops after COMPILE (no Higgsfield calls).

READS/WRITES: job.json (+ whatever each stage writes)

CONNECTS TO:
  - stages/* (each exposes NAME, run(ctx), is_complete(ctx))
  - core/context.py, core/workspace.py, models/job.py

INVARIANTS / GOTCHAS:
  - Exceptions mark the stage FAILED and propagate; re-running resumes there.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from tourgen.config import Settings
from tourgen.core.context import StageContext
from tourgen.core.workspace import Workspace
from tourgen.models.job import (
    STAGE_ORDER,
    JobInputs,
    JobOptions,
    JobState,
    StageName,
    StageState,
    StageStatus,
)

ProgressCallback = Callable[[StageName, StageState, str | None], None]


def stage_modules() -> dict[StageName, ModuleType]:
    # Imported lazily so `tourgen new` doesn't pay for cv2/anthropic imports.
    from tourgen.stages import (
        assemble,
        compile_prompts,
        deliver,
        generate,
        ingest,
        normalize,
        plan,
        qa,
        understand,
    )

    modules: dict[StageName, ModuleType] = {
        StageName.INGEST: ingest,
        StageName.NORMALIZE: normalize,
        StageName.UNDERSTAND: understand,
        StageName.PLAN: plan,
        StageName.COMPILE: compile_prompts,
        StageName.GENERATE: generate,
        StageName.QA: qa,
        StageName.ASSEMBLE: assemble,
        StageName.DELIVER: deliver,
    }
    return modules


def create_job(
    settings: Settings, job_id: str, inputs: JobInputs, options: JobOptions
) -> Workspace:
    workspace = Workspace.for_job(settings.jobs_dir, job_id)
    if workspace.exists():
        raise FileExistsError(f"Job '{job_id}' already exists at {workspace.root}")
    workspace.create()
    workspace.save_job(JobState(job_id=job_id, inputs=inputs, options=options))
    return workspace


def attach_job_log(workspace: Workspace) -> logging.Handler:
    handler = logging.FileHandler(workspace.log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("tourgen").addHandler(handler)
    return handler


def run_pipeline(
    ctx: StageContext,
    until: StageName | None = None,
    force: set[StageName] | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    force = force or set()
    modules = stage_modules()
    reset_downstream = False
    for name in STAGE_ORDER:
        status = ctx.job.stage(name)
        module = modules[name]
        if reset_downstream:
            ctx.job.stages[name] = status = StageStatus()
        complete = status.state == StageState.DONE and module.is_complete(ctx)
        if complete and name not in force:
            ctx.logger.info("stage %s: skipped (already done)", name)
            _notify(on_progress, name, StageState.DONE, "skipped")
        else:
            _run_stage(ctx, name, module, on_progress)
            reset_downstream = True
        if name == until:
            break
        if name == StageName.COMPILE and ctx.job.options.dry_run:
            ctx.logger.info("dry run: stopping before generation")
            break
    ctx.save_job()


def _run_stage(
    ctx: StageContext,
    name: StageName,
    module: ModuleType,
    on_progress: ProgressCallback | None,
) -> None:
    status = ctx.job.stage(name)
    status.state = StageState.RUNNING
    status.started_at = datetime.now(UTC)
    status.error = None
    ctx.save_job()
    _notify(on_progress, name, StageState.RUNNING, None)
    ctx.logger.info("stage %s: running", name)
    try:
        result = module.run(ctx)
    except Exception as exc:
        status.state = StageState.FAILED
        status.error = f"{type(exc).__name__}: {exc}"
        status.finished_at = datetime.now(UTC)
        ctx.job.errors.append(f"{name}: {status.error}")
        ctx.save_job()
        _notify(on_progress, name, StageState.FAILED, status.error)
        raise
    status.state = StageState.DONE
    status.summary = result.summary
    status.finished_at = datetime.now(UTC)
    ctx.save_job()
    ctx.logger.info("stage %s: done — %s", name, result.summary)
    _notify(on_progress, name, StageState.DONE, result.summary)


def _notify(
    callback: ProgressCallback | None, name: StageName, state: StageState, msg: str | None
) -> None:
    if callback is not None:
        callback(name, state, msg)


def open_context(settings: Settings, job_id: str) -> StageContext:
    workspace = Workspace.for_job(settings.jobs_dir, job_id)
    if not workspace.exists():
        raise FileNotFoundError(f"No job '{job_id}' under {Path(settings.jobs_dir).resolve()}")
    return StageContext(workspace=workspace, settings=settings, job=workspace.load_job())
