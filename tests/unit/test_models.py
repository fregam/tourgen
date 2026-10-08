"""Round-trip and validator tests for tourgen/models/*."""

from __future__ import annotations

from tourgen.models.analysis import Adjacency, PropertyModel, Room
from tourgen.models.assets import Asset, AssetIndex, AssetKind
from tourgen.models.generation import GenerationRecord, RequestStatus
from tourgen.models.job import JobState, StageName, StageState
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, TourPlan
from tourgen.models.qa import Hallucination, HallucinationSeverity, QAReport, Verdict


def _shot() -> Shot:
    return Shot(
        shot_id="s01",
        room_id="kitchen",
        source_asset_ids=["img_001"],
        camera_move=CameraMove(type=CameraMoveType.DOLLY_IN, strength=0.3),
        duration_s=4,
        model_id="kling-3.0",
        purpose="show kitchen",
    )


def test_tour_plan_round_trip() -> None:
    plan = TourPlan(title="t", shots=[_shot()], target_length_s=30, disclosure_text="AI")
    again = TourPlan.model_validate_json(plan.model_dump_json())
    assert again == plan
    assert again.total_duration_s == 4


def test_job_state_defaults_and_round_trip() -> None:
    job = JobState(job_id="x")
    assert all(s.state == StageState.PENDING for s in job.stages.values())
    assert list(job.stages) == list(StageName)
    assert JobState.model_validate_json(job.model_dump_json()).job_id == "x"


def test_asset_index_usable_excludes_duplicates() -> None:
    index = AssetIndex(
        assets=[
            Asset(asset_id="a", kind=AssetKind.PHOTO, source="s", original_name="a.jpg",
                  input_path="inputs/a.jpg", normalized_path="assets/a.jpg"),
            Asset(asset_id="b", kind=AssetKind.PHOTO, source="s", original_name="b.jpg",
                  input_path="inputs/b.jpg", normalized_path="assets/b.jpg", duplicate_of="a"),
        ]
    )  # fmt: skip
    assert [a.asset_id for a in index.usable_images()] == ["a"]
    assert AssetIndex.model_validate_json(index.model_dump_json()) == index


def test_structural_hallucination_never_passes() -> None:
    report = QAReport(
        shot_id="s01",
        attempt=1,
        fidelity_score=0.95,
        verdict=Verdict.PASS,
        hallucinations=[
            Hallucination(description="new window", severity=HallucinationSeverity.STRUCTURAL)
        ],
    )
    assert report.verdict == Verdict.RETRY


def test_property_model_adjacency() -> None:
    pm = PropertyModel(
        rooms=[Room(room_id="a", room_type="x", label="A", asset_ids=["1"]),
               Room(room_id="b", room_type="y", label="B", asset_ids=["2"])],
        adjacencies=[Adjacency(room_a="b", room_b="a", confidence=0.6)],
    )  # fmt: skip
    assert pm.are_adjacent("a", "b")
    assert not pm.are_adjacent("a", "b", min_confidence=0.9)
    assert pm.room_of("2") is not None


def test_request_status_terminal() -> None:
    assert RequestStatus.NSFW.is_terminal
    assert not RequestStatus.QUEUED.is_terminal
    record = GenerationRecord(
        shot_id="s", attempt=1, model_id="m", endpoint="e", idempotency_key="k"
    )
    assert GenerationRecord.model_validate_json(record.model_dump_json()) == record
