"""QA loop: injected bad clips are caught and retried or fall back (Phase 4 DoD)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import MakeCtx
from tourgen.core.pipeline import run_pipeline
from tourgen.generation.fake_client import FakeHiggsfieldClient
from tourgen.llm.fake_llm import FakeLLM
from tourgen.models.generation import GenerationPayload
from tourgen.models.job import StageName
from tourgen.models.qa import QAReport, ShotOutcomeIndex, Verdict


def test_retry_then_pass_and_reject_fallback(make_ctx: MakeCtx, tmp_path: Path) -> None:
    llm = FakeLLM(qa_script={"s02": ["retry", "pass"], "s03": ["reject"], "s04": ["retry"] * 5})
    video = FakeHiggsfieldClient(tmp_path / "hf", width=320, height=180, bad_shots={"s02"})
    ctx = make_ctx(llm=llm, video=video)
    run_pipeline(ctx, until=StageName.QA)
    ws = ctx.workspace
    outcomes = ws.read_model(ws.outcomes_file, ShotOutcomeIndex)

    s02 = outcomes.get("s02")
    assert s02 and s02.verdict == Verdict.PASS and s02.attempts == 2 and not s02.used_fallback
    retry = ws.read_model(ws.payload_file("s02", 2), GenerationPayload)
    first = ws.read_model(ws.payload_file("s02", 1), GenerationPayload)
    assert retry.duration_s <= first.duration_s  # shortened / calmer
    assert ws.clip_file("s02", 1).exists()  # failed attempt kept for debugging

    s03 = outcomes.get("s03")
    assert s03 and s03.used_fallback and s03.attempts == 1
    assert ws.kenburns_file("s03").exists()

    s04 = outcomes.get("s04")
    assert s04 and s04.used_fallback and s04.attempts == 3  # 1 + max_retries
    assert ws.read_model(ws.qa_report_file("s04", 3), QAReport).verdict == Verdict.RETRY

    calls = len(llm.calls)
    run_pipeline(ctx, until=StageName.QA)  # resume: no new QA calls
    assert len(llm.calls) == calls


def test_failed_and_nsfw_generations_fall_back(make_ctx: MakeCtx, tmp_path: Path) -> None:
    video = FakeHiggsfieldClient(
        tmp_path / "hf", width=320, height=180, fail_shots={"s01": "nsfw", "s02": "failed"}
    )
    ctx = make_ctx(video=video)
    run_pipeline(ctx, until=StageName.QA)
    outcomes = ctx.workspace.read_model(ctx.workspace.outcomes_file, ShotOutcomeIndex)
    for sid in ("s01", "s02"):
        outcome = outcomes.get(sid)
        assert outcome and outcome.used_fallback and outcome.attempts == 3
    report = ctx.workspace.read_model(ctx.workspace.qa_report_file("s01", 1), QAReport)
    assert report.adjustment.simplify_move and "nsfw" in report.reasons[0]
