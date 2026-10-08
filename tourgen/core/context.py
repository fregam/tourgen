"""
FILE: tourgen/core/context.py
ROLE: StageContext (what every stage receives) and StageResult (what it returns).

HOW IT WORKS:
  - StageContext bundles the workspace, settings, defaults, job state, model
    registry and lazily-built external clients (LLM, video generator).
  - Clients are chosen from job options: fake_llm → llm/fake_llm.py,
    fake_generation → generation/fake_client.py. Tests inject fakes directly.

CONNECTS TO:
  - Built by core/pipeline.py (and tests); passed to every stages/*.run(ctx).

INVARIANTS / GOTCHAS:
  - Stages must call ctx.save_job() after mutating ctx.job (e.g. the ledger).
  - Client construction is locked: worker threads must share ONE client instance.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tourgen.config import Settings, TourDefaults, load_defaults
from tourgen.core.workspace import Workspace
from tourgen.models.job import CostEntry, JobState

if TYPE_CHECKING:
    from tourgen.generation.base import VideoClient
    from tourgen.generation.registry import ModelRegistry
    from tourgen.llm.base import LLMClient


@dataclass
class StageResult:
    summary: str


@dataclass
class StageContext:
    workspace: Workspace
    settings: Settings
    job: JobState
    defaults: TourDefaults = field(default_factory=load_defaults)
    llm_override: LLMClient | None = None
    video_override: VideoClient | None = None
    registry_override: ModelRegistry | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("tourgen"))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _init_lock: threading.Lock = field(default_factory=threading.Lock)
    _llm: LLMClient | None = None
    _video: VideoClient | None = None
    _registry: ModelRegistry | None = None

    @property
    def registry(self) -> ModelRegistry:
        if self.registry_override is not None:
            return self.registry_override
        if self._registry is None:
            from tourgen.generation.registry import load_registry

            self._registry = load_registry(self.settings.models_yaml)
        return self._registry

    @property
    def llm(self) -> LLMClient:
        if self.llm_override is not None:
            return self.llm_override
        with self._init_lock:
            return self._build_llm()

    def _build_llm(self) -> LLMClient:
        if self._llm is None:
            if self.job.options.fake_llm:
                from tourgen.llm.fake_llm import FakeLLM

                self._llm = FakeLLM()
            else:
                from tourgen.llm.claude import ClaudeClient

                self._llm = ClaudeClient(
                    self.settings, log_dir=self.workspace.llm_log_dir, on_cost=self.record_cost
                )
        return self._llm

    @property
    def video(self) -> VideoClient:
        if self.video_override is not None:
            return self.video_override
        with self._init_lock:
            return self._build_video()

    def _build_video(self) -> VideoClient:
        if self._video is None:
            if self.job.options.fake_generation:
                from tourgen.generation.fake_client import FakeHiggsfieldClient

                self._video = FakeHiggsfieldClient(work_dir=self.workspace.work_dir / "fake_hf")
            else:
                from tourgen.generation.higgsfield_client import HiggsfieldClient

                self._video = HiggsfieldClient(self.settings)
        return self._video

    def record_cost(self, provider: str, description: str, usd: float) -> None:
        with self._lock:
            self.job.ledger.entries.append(
                CostEntry(provider=provider, description=description, usd=usd)
            )
            self.workspace.save_job(self.job)

    def save_job(self) -> None:
        with self._lock:
            self.workspace.save_job(self.job)
