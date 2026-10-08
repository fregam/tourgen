"""
FILE: tests/conftest.py
ROLE: Shared pytest fixtures: offline settings, fake clients, ready-made StageContexts.

HOW IT WORKS:
  - `settings` points jobs_dir at tmp_path, removes polling delays and renders at
    a small scale so ffmpeg work stays fast.
  - `make_ctx` creates a job on the fixture photos with FakeLLM + FakeHiggsfieldClient.

INVARIANTS / GOTCHAS:
  - No test may reach Claude or Higgsfield; everything goes through fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tourgen.config import Settings
from tourgen.core.context import StageContext
from tourgen.core.pipeline import create_job
from tourgen.generation.fake_client import FakeHiggsfieldClient
from tourgen.llm.fake_llm import FakeLLM
from tourgen.models.job import Brief, JobInputs, JobOptions

FIXTURES = Path(__file__).parent / "fixtures"
PHOTOS = FIXTURES / "photos"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        jobs_dir=tmp_path / "jobs",
        hf_poll_initial_s=0.0,
        hf_poll_max_s=0.0,
        hf_concurrency=3,
        render_scale=0.2,
        music_dir=tmp_path / "no_music",
        anthropic_api_key=None,
        hf_key=None,
    )


MakeCtx = Callable[..., StageContext]


@pytest.fixture
def make_ctx(settings: Settings, tmp_path: Path) -> MakeCtx:
    def _make(
        job_id: str = "t1",
        llm: FakeLLM | None = None,
        video: FakeHiggsfieldClient | None = None,
        options: JobOptions | None = None,
        paths: list[Path] | None = None,
        brief: Brief | None = None,
    ) -> StageContext:
        options = options or JobOptions(fake_generation=True, fake_llm=True, max_retries=2)
        inputs = JobInputs(
            paths=[str(p) for p in (paths or [PHOTOS])],
            brief=brief or Brief(target_length_s=30),
        )
        ws = create_job(settings, job_id, inputs, options)
        return StageContext(
            workspace=ws,
            settings=settings,
            job=ws.load_job(),
            llm_override=llm or FakeLLM(),
            video_override=video
            or FakeHiggsfieldClient(work_dir=tmp_path / "fake", width=320, height=180),
        )

    return _make
