"""Scheduler concurrency/backoff/persistence and video keyframe ingest."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import PHOTOS, MakeCtx
from tourgen.core.pipeline import run_pipeline
from tourgen.generation.fake_client import FakeHiggsfieldClient
from tourgen.generation.scheduler import GenerationScheduler, idempotency_key
from tourgen.media.ffmpeg import run_ffmpeg
from tourgen.models.assets import AssetIndex, AssetKind
from tourgen.models.generation import GenerationPayload, GenerationRecord, RequestStatus
from tourgen.models.job import StageName
from tourgen.models.plan import TourPlan


def test_concurrency_errors_are_retried_and_limit_respected(
    make_ctx: MakeCtx, tmp_path: Path
) -> None:
    video = FakeHiggsfieldClient(
        tmp_path / "hf", width=160, height=90, polls_to_complete=3,
        concurrency_limit=3, concurrency_errors=4,
    )  # fmt: skip
    ctx = make_ctx(video=video)
    run_pipeline(ctx, until=StageName.COMPILE)
    ws = ctx.workspace
    plan = ws.read_model(ws.tour_plan_file, TourPlan)
    payloads = [ws.read_model(ws.payload_file(s.shot_id, 1), GenerationPayload) for s in plan.shots]
    records = GenerationScheduler(ctx, sleep=lambda _: None).run(payloads)
    assert all(r.status == RequestStatus.COMPLETED for r in records)
    assert len(video.requests) == len(plan.shots)
    assert len(video.uploads) == len({a for p in payloads for a in p.image_asset_ids})
    record = ws.read_model(ws.record_file("s01", 1), GenerationRecord)
    assert record.idempotency_key == idempotency_key(ws.job_id, "s01", 1)
    assert record.local_path and ws.abs(record.local_path).exists()


def test_video_input_yields_keyframes(make_ctx: MakeCtx, tmp_path: Path) -> None:
    video = tmp_path / "walkthrough.mp4"
    run_ffmpeg([
        "-loop", "1", "-t", "1.5", "-i", str(PHOTOS / "kitchen.jpg"),
        "-loop", "1", "-t", "1.5", "-i", str(PHOTOS / "bedroom.jpg"),
        "-filter_complex", "[0:v]scale=640:426,setsar=1[a];[1:v]scale=640:426,setsar=1[b];"
        "[a][b]concat=n=2:v=1[v]", "-map", "[v]", "-r", "10", "-pix_fmt", "yuv420p", str(video),
    ])  # fmt: skip
    ctx = make_ctx(paths=[video])
    run_pipeline(ctx, until=StageName.NORMALIZE)
    index = ctx.workspace.read_model(ctx.workspace.asset_index_file, AssetIndex)
    frames = [a for a in index.assets if a.kind == AssetKind.VIDEO_FRAME]
    assert index.assets[0].kind == AssetKind.VIDEO and index.assets[0].duration_s
    assert len(frames) >= 4
    usable = index.usable_images()
    assert 2 <= len(usable) < len(frames)  # near-identical frames deduped
