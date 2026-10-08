"""End-to-end pipeline with FakeLLM + FakeHiggsfieldClient (BUILD_PROMPT Phase 5 DoD)."""

from __future__ import annotations

import pytest

from tests.conftest import PHOTOS, MakeCtx
from tourgen.core.costs import ConfirmationRequired
from tourgen.core.pipeline import run_pipeline
from tourgen.generation.fake_client import FakeHiggsfieldClient
from tourgen.media.ffmpeg import probe
from tourgen.models.delivery import Manifest
from tourgen.models.job import JobOptions, StageName, StageState
from tourgen.models.plan import TourPlan


def test_full_run_then_resume_without_duplicate_charges(make_ctx: MakeCtx) -> None:
    ctx = make_ctx()
    run_pipeline(ctx)
    ws = ctx.workspace
    assert all(s.state == StageState.DONE for s in ctx.job.stages.values())

    plan = ws.read_model(ws.tour_plan_file, TourPlan)
    assert plan.shots and plan.shots[0].room_id == "exterior_front"
    manifest = ws.read_model(ws.manifest_file, Manifest)
    assert [c.shot_id for c in manifest.clips] == [s.shot_id for s in plan.shots]
    assert manifest.disclosure_text
    assert all(c.request_id for c in manifest.clips)
    for aspect in ("16:9", "9:16"):
        info = probe(ws.final_render_file(aspect))
        assert info.has_audio
        assert (
            (info.width or 0) > (info.height or 0)
            if aspect == "16:9"
            else (info.height or 0) > (info.width or 0)
        )
    assert "QA" in ws.report_file.read_text()

    video = ctx.video
    assert isinstance(video, FakeHiggsfieldClient)
    submits = video.submit_calls
    run_pipeline(ctx)  # resume: everything skips
    assert video.submit_calls == submits


def test_resume_after_crash_reuses_request_ids(make_ctx: MakeCtx) -> None:
    ctx = make_ctx()
    run_pipeline(ctx, until=StageName.GENERATE)
    video = ctx.video
    assert isinstance(video, FakeHiggsfieldClient)
    submits = video.submit_calls
    # Simulate a crash mid-generation: stage marked running, one clip missing.
    ctx.job.stages[StageName.GENERATE].state = StageState.RUNNING
    ctx.workspace.clip_file("s01", 1).unlink()
    run_pipeline(ctx, until=StageName.GENERATE)
    assert (
        video.submit_calls == submits
    )  # re-downloaded from the persisted request, not resubmitted
    assert ctx.workspace.clip_file("s01", 1).exists()


def test_dry_run_stops_before_generation(make_ctx: MakeCtx) -> None:
    ctx = make_ctx(options=JobOptions(fake_generation=True, fake_llm=True, dry_run=True))
    run_pipeline(ctx)
    assert ctx.job.stages[StageName.COMPILE].state == StageState.DONE
    assert ctx.job.stages[StageName.GENERATE].state == StageState.PENDING
    assert list(ctx.workspace.payloads_dir.glob("*.json"))
    assert not list(ctx.workspace.clips_dir.glob("*.mp4"))


def test_paid_generation_requires_confirmation(make_ctx: MakeCtx) -> None:
    ctx = make_ctx(options=JobOptions(fake_generation=False, fake_llm=True, budget_usd=500))
    ctx.settings.confirm_above_usd = 0.01
    with pytest.raises(ConfirmationRequired):
        run_pipeline(ctx)
    assert ctx.job.stages[StageName.GENERATE].state == StageState.FAILED
    video = ctx.video
    assert isinstance(video, FakeHiggsfieldClient) and video.submit_calls == 0
    ctx.job.options.approved_cost_usd = 500
    run_pipeline(ctx, until=StageName.GENERATE)
    assert video.submit_calls > 0
    assert ctx.job.ledger.total("higgsfield") > 0


def test_photos_fixture_present() -> None:
    assert len(list(PHOTOS.glob("*.jpg"))) >= 10
