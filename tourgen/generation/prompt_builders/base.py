"""
FILE: tourgen/generation/prompt_builders/base.py
ROLE: Shared prompt wording + payload assembly used by every model-family builder.

HOW IT WORKS:
  - compose_prompt(): one camera instruction + one light cue + preservation clauses
    (BUILD_PROMPT §3 step 5). Fits the model's prompt limit, dropping optional
    clauses before ever dropping preservation.
  - build_payload(): maps shot fields onto the registry's field names
    (first/last frame, duration, resolution, negative prompt) → GenerationPayload.
  - apply_retry_adjustment(): turns a QA RetryAdjustment into a calmer Shot.

CONNECTS TO:
  - prompt_builders/kling.py, seedance.py, wan.py; stages/compile_prompts.py

INVARIANTS / GOTCHAS:
  - Fidelity rule: prompts never ask to add/remove/restyle/improve anything.
  - Local images are referenced as "asset://<id>"; the scheduler uploads them.
"""

from __future__ import annotations

from tourgen.generation.registry import ModelRegistry, ModelSpec
from tourgen.models.analysis import Framing, ImageAnalysis
from tourgen.models.generation import ASSET_REF_PREFIX, GenerationPayload
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, ShotType
from tourgen.models.qa import RetryAdjustment

MOVE_PHRASES: dict[CameraMoveType, str] = {
    CameraMoveType.DOLLY_IN: "camera dollies forward toward the centre of the scene",
    CameraMoveType.DOLLY_OUT: "camera dollies backward, slowly widening the view",
    CameraMoveType.TRUCK_LEFT: "camera tracks sideways to the left, parallel to the scene",
    CameraMoveType.TRUCK_RIGHT: "camera tracks sideways to the right, parallel to the scene",
    CameraMoveType.PAN_LEFT: "camera pans gently to the left from a fixed position",
    CameraMoveType.PAN_RIGHT: "camera pans gently to the right from a fixed position",
    CameraMoveType.TILT_UP: "camera tilts gently upward from a fixed position",
    CameraMoveType.TILT_DOWN: "camera tilts gently downward from a fixed position",
    CameraMoveType.ORBIT_LEFT: "camera makes a small arc to the left around the central feature",
    CameraMoveType.ORBIT_RIGHT: "camera makes a small arc to the right around the central feature",
    CameraMoveType.CRANE_UP: "camera rises slowly upward like a crane shot",
    CameraMoveType.STATIC: "camera stays almost still with a barely perceptible drift",
}

PRESERVATION = (
    "Keep the architecture, layout, walls, windows, doors, furniture and materials exactly "
    "as in the image. No new objects, no people, no animals, no text. Do not restyle, "
    "redecorate or change colours."
)
TRANSITION_CLAUSE = (
    "Continuous single camera move from the first frame to the last frame through the real "
    "opening that connects these two spaces; both spaces must stay exactly as photographed."
)
NEGATIVE_PROMPT = (
    "people, animals, new furniture, extra windows, extra doors, warped walls, bent lines, "
    "distorted geometry, text, watermark, fast motion, camera shake, flicker"
)


def speed_word(strength: float) -> str:
    if strength < 0.25:
        return "Very slow, subtle"
    if strength < 0.55:
        return "Slow, smooth"
    return "Steady, smooth"


def light_cue(analysis: ImageAnalysis | None) -> str:
    lighting = analysis.lighting.strip().rstrip(".") if analysis and analysis.lighting else ""
    base = (
        f"Lighting stays as photographed ({lighting})"
        if lighting
        else "Lighting stays as photographed"
    )
    return base + ", stable exposure, no flicker."


def compose_prompt(
    shot: Shot,
    analysis: ImageAnalysis | None,
    limit: int,
    extra_preservation: list[str] | None = None,
) -> str:
    camera = f"{speed_word(shot.camera_move.strength)} {MOVE_PHRASES[shot.camera_move.type]}."
    required = [camera, light_cue(analysis), PRESERVATION]
    if shot.shot_type == ShotType.FRAME_TRANSITION:
        required.insert(1, TRANSITION_CLAUSE)
    optional = [f"{clause.strip().rstrip('.')}." for clause in (extra_preservation or [])]
    text = " ".join(required)
    if len(text) > limit:
        # Preservation is never dropped; shorten the camera wording instead.
        text = " ".join([PRESERVATION, camera])[:limit]
        return text
    for clause in optional:
        if len(text) + 1 + len(clause) <= limit:
            text = f"{text} {clause}"
    return text


def build_payload(
    shot: Shot,
    spec: ModelSpec,
    attempt: int,
    prompt: str,
    extra_arguments: dict[str, object] | None = None,
) -> GenerationPayload:
    duration = spec.clamp_duration(shot.duration_s)
    arguments: dict[str, object] = {
        spec.first_frame_field: ASSET_REF_PREFIX + shot.source_asset_ids[0],
        spec.prompt_field: prompt,
        spec.duration_field: int(duration) if spec.integer_durations else duration,
    }
    if shot.shot_type == ShotType.FRAME_TRANSITION:
        if not (spec.supports_last_frame and spec.last_frame_field):
            raise ValueError(f"{spec.model_id} has no last-frame field (shot {shot.shot_id})")
        arguments[spec.last_frame_field] = ASSET_REF_PREFIX + shot.source_asset_ids[1]
    if spec.resolution_field and spec.default_resolution:
        arguments[spec.resolution_field] = spec.default_resolution
    if spec.negative_prompt_field:
        arguments[spec.negative_prompt_field] = NEGATIVE_PROMPT
    arguments.update(extra_arguments or {})
    return GenerationPayload(
        shot_id=shot.shot_id,
        attempt=attempt,
        model_id=spec.model_id,
        endpoint=spec.endpoint,
        arguments=arguments,
        prompt=prompt,
        duration_s=duration,
        image_asset_ids=list(shot.source_asset_ids),
        estimated_cost_usd=round(duration * spec.usd_per_second, 4),
    )


def apply_retry_adjustment(
    shot: Shot,
    adjustment: RetryAdjustment,
    registry: ModelRegistry,
    analysis: ImageAnalysis | None,
) -> Shot:
    """Return a calmer copy of `shot` following QA feedback (never more aggressive)."""
    new = shot.model_copy(deep=True)
    if adjustment.reduce_motion:
        new.camera_move = CameraMove(
            type=new.camera_move.type, strength=round(new.camera_move.strength * 0.5, 3)
        )
    if adjustment.simplify_move:
        detail = analysis is not None and analysis.framing == Framing.DETAIL
        new.camera_move = CameraMove(
            type=CameraMoveType.STATIC if detail else CameraMoveType.DOLLY_IN,
            strength=min(new.camera_move.strength, 0.3),
        )
        if new.shot_type == ShotType.FRAME_TRANSITION:
            new.shot_type = ShotType.SINGLE_IMAGE
            new.source_asset_ids = new.source_asset_ids[:1]
    if adjustment.switch_model and adjustment.switch_model in registry.models:
        new.model_id = adjustment.switch_model
    spec = registry.get(new.model_id)
    if new.shot_type == ShotType.FRAME_TRANSITION and not spec.supports_last_frame:
        new.shot_type = ShotType.SINGLE_IMAGE
        new.source_asset_ids = new.source_asset_ids[:1]
    if adjustment.shorten_duration:
        new.duration_s = max(spec.duration_min_s, new.duration_s - 1.5)
    new.duration_s = spec.clamp_duration(new.duration_s)
    return new
