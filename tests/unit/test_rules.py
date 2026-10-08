"""core/rules.py: validation and repair of tour plans."""

from __future__ import annotations

import pytest

from tourgen.config import CONFIG_DIR
from tourgen.core.rules import errors, repair_plan, validate_plan
from tourgen.generation.registry import load_registry
from tourgen.models.analysis import (
    Adjacency,
    Framing,
    ImageAnalysis,
    ImageAnalysisSet,
    PropertyModel,
    Room,
)
from tourgen.models.assets import Asset, AssetIndex, AssetKind
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, ShotType, TourPlan

REGISTRY = load_registry(CONFIG_DIR / "models.yaml")
World = tuple[PropertyModel, ImageAnalysisSet, AssetIndex]


def _asset(aid: str) -> Asset:
    return Asset(asset_id=aid, kind=AssetKind.PHOTO, source="x", original_name=f"{aid}.jpg",
                 input_path=f"inputs/{aid}.jpg", normalized_path=f"assets/{aid}.jpg")  # fmt: skip


def _analysis(aid: str, framing: Framing = Framing.WIDE) -> ImageAnalysis:
    return ImageAnalysis(asset_id=aid, room_type="room", is_indoor=True, features=[],
                         framing=framing, lighting="day", motion_suitability=0.8,
                         safe_camera_moves=[CameraMoveType.DOLLY_IN])  # fmt: skip


@pytest.fixture
def world() -> World:
    pm = PropertyModel(
        rooms=[
            Room(room_id="living", room_type="living_room", label="Living", asset_ids=["a"]),
            Room(room_id="kitchen", room_type="kitchen", label="Kitchen", asset_ids=["b"]),
            Room(room_id="bath", room_type="bathroom", label="Bath", asset_ids=["c"]),
        ],
        adjacencies=[Adjacency(room_a="living", room_b="kitchen", confidence=0.8)],
    )
    analyses = ImageAnalysisSet(
        analyses=[_analysis("a"), _analysis("b"), _analysis("c", Framing.DETAIL)]
    )
    return pm, analyses, AssetIndex(assets=[_asset("a"), _asset("b"), _asset("c")])


def _shot(sid: str, assets: list[str], **kw: object) -> Shot:
    data: dict[str, object] = dict(
        shot_id=sid, room_id="living", source_asset_ids=assets,
        camera_move=CameraMove(type=CameraMoveType.DOLLY_IN, strength=0.4),
        duration_s=5, model_id="kling-3.0", purpose="p",
    )  # fmt: skip
    data.update(kw)
    return Shot.model_validate(data)


def _plan(*shots: Shot) -> TourPlan:
    return TourPlan(title="t", shots=list(shots), target_length_s=15, disclosure_text="AI")


def test_valid_plan_passes(world: World) -> None:
    plan = _plan(
        _shot("s1", ["a"]),
        _shot("s2", ["a", "b"], shot_type=ShotType.FRAME_TRANSITION),
        _shot("s3", ["b"], room_id="kitchen"),
    )
    pm, analyses, index = world
    assert errors(validate_plan(plan, pm, analyses, REGISTRY, index)) == []


def test_violations_detected(world: World) -> None:
    pm, analyses, index = world
    plan = _plan(
        _shot("s1", ["a", "c"], shot_type=ShotType.FRAME_TRANSITION),  # not adjacent
        _shot("s1", ["zzz"]),  # duplicate id + unknown asset
        _shot("s3", ["b"], duration_s=40, room_id="kitchen"),  # out of range
        _shot(
            "s4",
            ["c"],
            room_id="bath",
            camera_move=CameraMove(type=CameraMoveType.DOLLY_IN, strength=0.6),
        ),
        _shot("s5", ["a", "b"], shot_type=ShotType.FRAME_TRANSITION, model_id="kling-3.0-turbo"),
    )
    plan.disclosure_text = " "
    codes = {v.code for v in errors(validate_plan(plan, pm, analyses, REGISTRY, index))}
    assert {
        "transition_not_adjacent", "duplicate_shot_id", "unknown_asset", "duration_out_of_range",
        "detail_too_much_motion", "no_last_frame", "no_disclosure",
    } <= codes  # fmt: skip


def test_repair_makes_plan_valid(world: World) -> None:
    pm, analyses, index = world
    plan = _plan(
        _shot("s1", ["a", "c"], shot_type=ShotType.FRAME_TRANSITION),
        _shot("s2", ["zzz"]),
        _shot("s3", ["b"], duration_s=40, room_id="nowhere", model_id="unknown"),
        _shot(
            "s4",
            ["c"],
            room_id="bath",
            camera_move=CameraMove(type=CameraMoveType.PAN_LEFT, strength=0.9),
        ),
    )
    plan.disclosure_text = ""
    fixed = repair_plan(plan, pm, analyses, REGISTRY, index, "kling-3.0", "AI disclosure")
    assert [s.shot_id for s in fixed.shots] == ["s1", "s3", "s4"]
    assert fixed.shots[0].shot_type == ShotType.SINGLE_IMAGE
    assert fixed.shots[0].source_asset_ids == ["a"]
    assert fixed.shots[1].room_id == "kitchen" and fixed.shots[1].duration_s == 15
    assert fixed.shots[2].camera_move.strength == 0.3
    assert errors(validate_plan(fixed, pm, analyses, REGISTRY, index)) == []
