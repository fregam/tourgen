"""generation/prompt_builders: wording, limits, field mapping, retry adjustments."""

from __future__ import annotations

import pytest

from tourgen.config import CONFIG_DIR
from tourgen.generation.prompt_builders import builder_for
from tourgen.generation.prompt_builders.base import (
    PRESERVATION,
    apply_retry_adjustment,
    compose_prompt,
)
from tourgen.generation.registry import load_registry
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, ShotType
from tourgen.models.qa import RetryAdjustment

REGISTRY = load_registry(CONFIG_DIR / "models.yaml")
BANNED = ("add ", "remove", "restyle", "improve", "stage ")


def _shot(**kw: object) -> Shot:
    data: dict[str, object] = dict(
        shot_id="s01", room_id="kitchen", source_asset_ids=["img_001"],
        camera_move=CameraMove(type=CameraMoveType.TRUCK_LEFT, strength=0.4),
        duration_s=4.6, model_id="kling-3.0", purpose="p",
    )  # fmt: skip
    data.update(kw)
    return Shot.model_validate(data)


@pytest.mark.parametrize("model_id", list(REGISTRY.models))
def test_every_model_builds_valid_payload(model_id: str) -> None:
    spec = REGISTRY.get(model_id)
    payload = builder_for(spec)(_shot(model_id=model_id), spec, 1, None, ["keep the island"])
    assert payload.endpoint == spec.endpoint
    assert payload.arguments[spec.first_frame_field] == "asset://img_001"
    assert spec.duration_ok(payload.arguments[spec.duration_field])
    prompt = payload.arguments[spec.prompt_field]
    assert len(prompt) <= spec.prompt_max_chars
    assert PRESERVATION in prompt
    instructions = prompt.replace(PRESERVATION, "").lower()
    assert not any(word in instructions for word in BANNED)


def test_transition_uses_last_frame_field() -> None:
    spec = REGISTRY.get("seedance-2.0")
    shot = _shot(shot_type=ShotType.FRAME_TRANSITION, source_asset_ids=["a", "b"])
    payload = builder_for(spec)(shot, spec, 2, None, [])
    assert payload.arguments["end_image_url"] == "asset://b"
    assert payload.attempt == 2


def test_transition_on_model_without_last_frame_raises() -> None:
    spec = REGISTRY.get("kling-3.0-turbo")
    shot = _shot(shot_type=ShotType.FRAME_TRANSITION, source_asset_ids=["a", "b"])
    with pytest.raises(ValueError):
        builder_for(spec)(shot, spec, 1, None, [])


def test_prompt_never_drops_preservation_under_tight_limit() -> None:
    prompt = compose_prompt(_shot(), None, limit=len(PRESERVATION) + 20)
    assert prompt.startswith(PRESERVATION)


def test_retry_adjustment_is_calmer() -> None:
    shot = _shot(shot_type=ShotType.FRAME_TRANSITION, source_asset_ids=["a", "b"], duration_s=6)
    adjusted = apply_retry_adjustment(
        shot,
        RetryAdjustment(reduce_motion=True, shorten_duration=True, switch_model="kling-3.0-turbo"),
        REGISTRY,
        None,
    )
    assert adjusted.camera_move.strength == pytest.approx(0.2)
    assert adjusted.duration_s == 4  # 6 - 1.5 = 4.5 → rounded to the integer grid
    assert adjusted.model_id == "kling-3.0-turbo"
    assert adjusted.shot_type == ShotType.SINGLE_IMAGE  # turbo has no last frame
    simplified = apply_retry_adjustment(
        _shot(), RetryAdjustment(simplify_move=True), REGISTRY, None
    )
    assert simplified.camera_move.type == CameraMoveType.DOLLY_IN
