"""
FILE: tourgen/api/app.py
ROLE: Phase 6 HTTP API + static single-page UI. Thin layer over core/pipeline.py.

HOW IT WORKS:
  - POST /api/jobs                 multipart: photos/videos and/or a listing `url`, optional
                                   `email` (comma-separated) + brief → creates a job
  - POST /api/jobs/{id}/plan       runs stages up to COMPILE in a background thread
  - GET  /api/jobs/{id}            job state, plan, estimate
  - PUT  /api/jobs/{id}/plan       reorder/drop shots (rule-validated), resets downstream stages
  - POST /api/jobs/{id}/approve    records the approved cost and runs the rest in background
  - GET  /api/jobs/{id}/events     Server-Sent Events: stage statuses until the current run ends
  - GET  /api/jobs/{id}/download/{aspect}  final MP4 (aspect "16x9" | "9x16")
  - GET  /                         tourgen/api/static/index.html

READS/WRITES: jobs/<id>/** through core/workspace.py only

CONNECTS TO:
  - core/pipeline.py, core/costs.py, core/rules.py; started by `tourgen serve`

INVARIANTS / GOTCHAS:
  - One background run per job at a time (409 otherwise).
  - Webhook receiver for Higgsfield completions is not implemented yet (PLAN 6.3);
    polling remains the mechanism.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from tourgen.config import Settings
from tourgen.core.context import StageContext
from tourgen.core.costs import estimate_job
from tourgen.core.pipeline import create_job, open_context, run_pipeline
from tourgen.core.rules import errors, validate_plan
from tourgen.ingest.local import classify
from tourgen.models.analysis import ImageAnalysisSet, PropertyModel
from tourgen.models.assets import AssetIndex
from tourgen.models.job import (
    STAGE_ORDER,
    Brief,
    JobInputs,
    JobOptions,
    StageName,
    StageStatus,
)
from tourgen.models.plan import TourPlan
from tourgen.notify.email import valid_email

STATIC_DIR = Path(__file__).parent / "static"


class PlanEdit(BaseModel):
    shot_ids: list[str]  # kept shots, in the new order


class Approval(BaseModel):
    approved_cost_usd: float


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="tourgen")
    running: dict[str, threading.Thread] = {}
    lock = threading.Lock()

    def ctx_for(job_id: str) -> StageContext:
        try:
            return open_context(settings, job_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    def start(job_id: str, until: StageName | None) -> None:
        with lock:
            if job_id in running and running[job_id].is_alive():
                raise HTTPException(409, "job is already running")

            def work() -> None:
                ctx = open_context(settings, job_id)
                # run_pipeline records any failure (incl. budget/confirmation) in job.json,
                # which is what the UI reads; nothing else to do with the exception here.
                with contextlib.suppress(Exception):
                    run_pipeline(ctx, until=until)

            thread = threading.Thread(target=work, daemon=True)
            running[job_id] = thread
            thread.start()

    def is_running(job_id: str) -> bool:
        thread = running.get(job_id)
        return thread is not None and thread.is_alive()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text()

    @app.post("/api/jobs")
    async def create(
        files: list[UploadFile] | None = File(None),
        url: str = Form(""),
        email: str = Form(""),
        brief: str = Form(""),
        format: str = Form("social"),
        fake_generation: bool = Form(False),
        fake_llm: bool = Form(False),
    ) -> dict[str, str]:
        recipients = [e.strip() for e in email.split(",") if e.strip()]
        bad = [e for e in recipients if not valid_email(e)]
        if bad:
            raise HTTPException(422, f"invalid email address: {', '.join(bad)}")
        urls = [url.strip()] if url.strip() else []
        if not files and not urls:
            raise HTTPException(422, "upload photos/videos or give a listing URL")
        job_id = datetime.now(UTC).strftime("web-%Y%m%d-%H%M%S-%f")
        options = JobOptions(
            fake_generation=fake_generation,
            fake_llm=fake_llm,
            max_shots=settings.max_shots,
            max_retries=settings.max_retries,
            budget_usd=settings.budget_usd,
        )
        ws = create_job(settings, job_id, JobInputs(), options)
        ws.uploads_dir.mkdir(parents=True, exist_ok=True)
        for upload in files or []:
            name = Path(upload.filename or "upload").name
            if classify(Path(name)) is None:
                continue
            with (ws.uploads_dir / name).open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
        job = ws.load_job()
        job.inputs = JobInputs(
            paths=[str(ws.uploads_dir)] if files else [],
            urls=urls,
            brief=Brief(text=brief, format=format),
            notify_email=recipients,
        )
        ws.save_job(job)
        return {"job_id": job_id}

    @app.post("/api/jobs/{job_id}/plan")
    def plan(job_id: str) -> dict[str, str]:
        ctx_for(job_id)
        start(job_id, StageName.COMPILE)
        return {"status": "planning"}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        ctx = ctx_for(job_id)
        ws = ctx.workspace
        tour = ws.read_model_or_none(ws.tour_plan_file, TourPlan)
        estimate = None
        if tour is not None:
            est = estimate_job(tour, ctx.registry, ctx.settings, ctx.job.options.max_retries)
            estimate = {"total_usd": est.total_usd, "worst_usd": est.worst_total_usd,
                        "lines": est.lines()}  # fmt: skip
        return {
            "job": json.loads(ctx.job.model_dump_json()),
            "plan": json.loads(tour.model_dump_json()) if tour else None,
            "estimate": estimate,
            "running": is_running(job_id),
            "outputs": [a for a in (tour.aspect_ratios if tour else [])
                        if ws.final_render_file(a).exists()],
        }  # fmt: skip

    @app.put("/api/jobs/{job_id}/plan")
    def edit_plan(job_id: str, edit: PlanEdit) -> dict[str, Any]:
        if is_running(job_id):
            raise HTTPException(409, "job is running")
        ctx = ctx_for(job_id)
        ws = ctx.workspace
        tour = ws.read_model_or_none(ws.tour_plan_file, TourPlan)
        if tour is None:
            raise HTTPException(400, "no plan yet")
        by_id = {s.shot_id: s for s in tour.shots}
        unknown = [sid for sid in edit.shot_ids if sid not in by_id]
        if unknown or not edit.shot_ids:
            raise HTTPException(422, f"unknown or empty shot ids: {unknown}")
        tour.shots = [by_id[sid] for sid in edit.shot_ids]
        violations = validate_plan(
            tour,
            ws.read_model(ws.property_model_file, PropertyModel),
            ws.read_model(ws.image_analyses_file, ImageAnalysisSet),
            ctx.registry,
            ws.read_model(ws.asset_index_file, AssetIndex),
        )
        if errors(violations):
            raise HTTPException(422, [str(v) for v in errors(violations)])
        ws.write_model(ws.tour_plan_file, tour)
        for name in STAGE_ORDER[STAGE_ORDER.index(StageName.COMPILE) :]:
            ctx.job.stages[name] = StageStatus()
        ctx.save_job()
        return {"shots": len(tour.shots), "warnings": [str(v) for v in violations]}

    @app.post("/api/jobs/{job_id}/approve")
    def approve(job_id: str, approval: Approval) -> dict[str, str]:
        ctx = ctx_for(job_id)
        ctx.job.options.approved_cost_usd = approval.approved_cost_usd
        ctx.job.options.dry_run = False
        ctx.save_job()
        start(job_id, None)
        return {"status": "running"}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str) -> StreamingResponse:
        ws = ctx_for(job_id).workspace

        async def stream() -> AsyncIterator[str]:
            last = ""
            while True:
                job = ws.load_job()
                payload = json.dumps(
                    {name.value: {"state": st.state.value, "summary": st.summary,
                                  "error": st.error} for name, st in job.stages.items()}
                )  # fmt: skip
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                # The stream follows one background run (plan-only or full) and ends with it.
                if not is_running(job_id):
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/jobs/{job_id}/download/{aspect}")
    def download(job_id: str, aspect: str) -> FileResponse:
        ws = ctx_for(job_id).workspace
        path = ws.final_render_file(aspect.replace("x", ":"))
        if not path.exists():
            raise HTTPException(404, "not rendered yet")
        return FileResponse(path, media_type="video/mp4", filename=f"{job_id}_{path.name}")

    return app


app = create_app()
