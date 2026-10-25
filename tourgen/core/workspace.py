"""
FILE: tourgen/core/workspace.py
ROLE: Every filesystem path of a job. The ONLY module that knows the layout in BUILD_PROMPT §3.

HOW IT WORKS:
  - Workspace(root) exposes path properties/methods and typed JSON read/write
    helpers (atomic write via temp file + rename).

READS/WRITES: jobs/<job_id>/**

CONNECTS TO:
  - Used by every stage, core/pipeline.py, cli.py, api/app.py

INVARIANTS / GOTCHAS:
  - Paths stored inside schemas are relative to the job root (rel()/abs()).
  - Writes are atomic so a crash never leaves half a JSON file that would
    confuse resume logic.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from tourgen.models.job import JobState

T = TypeVar("T", bound=BaseModel)

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @classmethod
    def for_job(cls, jobs_dir: Path, job_id: str) -> Workspace:
        if not _JOB_ID_RE.match(job_id):
            raise ValueError(f"Invalid job id: {job_id!r}")
        return cls(jobs_dir / job_id)

    @property
    def job_id(self) -> str:
        return self.root.name

    # --- directories -------------------------------------------------------
    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def llm_log_dir(self) -> Path:
        return self.analysis_dir / "_llm_log"

    @property
    def plan_dir(self) -> Path:
        return self.root / "plan"

    @property
    def payloads_dir(self) -> Path:
        return self.plan_dir / "payloads"

    @property
    def clips_dir(self) -> Path:
        return self.root / "clips"

    @property
    def qa_dir(self) -> Path:
        return self.root / "qa"

    @property
    def qa_frames_dir(self) -> Path:
        return self.qa_dir / "frames"

    @property
    def render_dir(self) -> Path:
        return self.root / "render"

    @property
    def uploads_dir(self) -> Path:
        """Files uploaded through the HTTP API before ingest copies them to inputs/."""
        return self.root / "_uploads"

    @property
    def work_dir(self) -> Path:
        """Scratch space for intermediate renders (normalized clips, cards)."""
        return self.render_dir / "_work"

    def create(self) -> None:
        for directory in (
            self.inputs_dir,
            self.assets_dir,
            self.analysis_dir,
            self.llm_log_dir,
            self.payloads_dir,
            self.clips_dir,
            self.qa_frames_dir,
            self.render_dir,
            self.work_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # --- files -------------------------------------------------------------
    @property
    def job_file(self) -> Path:
        return self.root / "job.json"

    @property
    def asset_index_file(self) -> Path:
        return self.assets_dir / "asset_index.json"

    @property
    def image_analyses_file(self) -> Path:
        return self.analysis_dir / "image_analyses.json"

    @property
    def property_model_file(self) -> Path:
        return self.analysis_dir / "property_model.json"

    @property
    def tour_plan_file(self) -> Path:
        return self.plan_dir / "tour_plan.json"

    @property
    def outcomes_file(self) -> Path:
        return self.qa_dir / "outcomes.json"

    @property
    def manifest_file(self) -> Path:
        return self.render_dir / "manifest.json"

    @property
    def report_file(self) -> Path:
        return self.render_dir / "report.md"

    @property
    def notify_file(self) -> Path:
        return self.render_dir / "notify.json"

    @property
    def email_dir(self) -> Path:
        """Email-sized copies of the renders and the .eml outbox."""
        return self.render_dir / "email"

    @property
    def log_file(self) -> Path:
        return self.root / "job.log"

    def payload_file(self, shot_id: str, attempt: int) -> Path:
        return self.payloads_dir / f"{shot_id}__attempt{attempt}.json"

    def clip_file(self, shot_id: str, attempt: int) -> Path:
        return self.clips_dir / f"{shot_id}__attempt{attempt}.mp4"

    def record_file(self, shot_id: str, attempt: int) -> Path:
        return self.clips_dir / f"{shot_id}__attempt{attempt}.record.json"

    def kenburns_file(self, shot_id: str) -> Path:
        return self.clips_dir / f"{shot_id}__kenburns.mp4"

    def qa_report_file(self, shot_id: str, attempt: int) -> Path:
        return self.qa_dir / f"{shot_id}__attempt{attempt}.qa.json"

    def qa_frames_for(self, shot_id: str, attempt: int) -> Path:
        return self.qa_frames_dir / f"{shot_id}__attempt{attempt}"

    def final_render_file(self, aspect_ratio: str) -> Path:
        return self.render_dir / f"final_{aspect_ratio.replace(':', 'x')}.mp4"

    # --- path conversion ---------------------------------------------------
    def rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def abs(self, relative: str) -> Path:
        return self.root / relative

    # --- typed JSON IO -----------------------------------------------------
    def write_model(self, path: Path, model: BaseModel) -> None:
        write_text_atomic(path, model.model_dump_json(indent=2))

    def read_model(self, path: Path, cls: type[T]) -> T:
        return cls.model_validate_json(path.read_text())

    def read_model_or_none(self, path: Path, cls: type[T]) -> T | None:
        if not path.exists():
            return None
        try:
            return self.read_model(path, cls)
        except ValueError:
            return None

    def load_job(self) -> JobState:
        return self.read_model(self.job_file, JobState)

    def save_job(self, job: JobState) -> None:
        self.write_model(self.job_file, job)

    def exists(self) -> bool:
        return self.job_file.exists()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
