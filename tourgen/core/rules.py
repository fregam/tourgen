"""
FILE: tourgen/core/rules.py
ROLE: Deterministic validation (and mechanical repair) of a TourPlan against BUILD_PROMPT §5
      and the model registry. Runs before any paid generation.

HOW IT WORKS:
  - validate_plan() returns RuleViolations (severity "error" blocks, "warning" informs).
  - repair_plan() fixes what is mechanical (clamp durations/strength, downgrade an
    invalid transition to a single-image shot, drop shots with unknown assets).
  - stages/plan.py first re-asks Claude with the violations, then repairs, then
    re-validates; remaining errors fail the stage.

CONNECTS TO:
  - stages/plan.py, stages/compile_prompts.py (sanity re-check), tests/unit/test_rules.py

INVARIANTS / GOTCHAS:
  - Pure functions; no IO. Strict mypy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tourgen.generation.registry import ModelRegistry
from tourgen.models.analysis import Framing, ImageAnalysisSet, PropertyModel
from tourgen.models.assets import AssetIndex
from tourgen.models.plan import CameraMove, CameraMoveType, ShotType, TourPlan

MAX_STRENGTH = 0.7
MAX_DETAIL_STRENGTH = 0.3
MIN_TRANSITION_CONFIDENCE = 0.5
LENGTH_TOLERANCE = 0.35


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str
    shot_id: str | None = None
    severity: Literal["error", "warning"] = "error"

    def __str__(self) -> str:
        where = f"[{self.shot_id}] " if self.shot_id else ""
        return f"{self.severity.upper()} {self.code}: {where}{self.message}"


def errors(violations: list[RuleViolation]) -> list[RuleViolation]:
    return [v for v in violations if v.severity == "error"]


def validate_plan(
    plan: TourPlan,
    property_model: PropertyModel,
    analyses: ImageAnalysisSet,
    registry: ModelRegistry,
    index: AssetIndex,
) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    if not plan.shots:
        out.append(RuleViolation("no_shots", "plan has no shots"))
    if not plan.disclosure_text.strip():
        out.append(RuleViolation("no_disclosure", "disclosure_text must not be empty"))
    usable = {a.asset_id for a in index.usable_images()}
    analysed = {a.asset_id for a in analyses.analyses}
    room_ids = {r.room_id for r in property_model.rooms}
    seen: set[str] = set()
    for shot in plan.shots:
        sid = shot.shot_id
        if sid in seen:
            out.append(RuleViolation("duplicate_shot_id", "shot_id is not unique", sid))
        seen.add(sid)
        if shot.room_id not in room_ids:
            out.append(RuleViolation("unknown_room", f"room '{shot.room_id}' not in model", sid))
        for asset_id in shot.source_asset_ids:
            if asset_id not in usable or asset_id not in analysed:
                out.append(RuleViolation("unknown_asset", f"'{asset_id}' is not usable", sid))
        if shot.model_id not in registry.models:
            out.append(RuleViolation("unknown_model", f"model '{shot.model_id}'", sid))
            continue
        spec = registry.get(shot.model_id)
        if not spec.duration_ok(shot.duration_s):
            out.append(
                RuleViolation(
                    "duration_out_of_range",
                    f"{shot.duration_s}s not in {spec.duration_min_s}-{spec.duration_max_s}s "
                    f"for {spec.model_id}",
                    sid,
                )
            )
        if shot.camera_move.strength > MAX_STRENGTH:
            out.append(RuleViolation("move_too_strong", "camera moves must be slow", sid))
        first = shot.source_asset_ids[0]
        if first in analysed:
            analysis = analyses.get(first)
            if (
                analysis.framing == Framing.DETAIL
                and shot.camera_move.strength > MAX_DETAIL_STRENGTH
            ):
                out.append(
                    RuleViolation("detail_too_much_motion", "detail photo moves too much", sid)
                )
            if (
                shot.camera_move.type != CameraMoveType.STATIC
                and shot.camera_move.type not in analysis.safe_camera_moves
            ):
                out.append(
                    RuleViolation(
                        "unsafe_move",
                        f"{shot.camera_move.type} not in safe moves for {first}",
                        sid,
                        "warning",
                    )
                )
        out += _check_shot_shape(shot.shot_type, shot.source_asset_ids, sid)
        if shot.shot_type == ShotType.FRAME_TRANSITION and len(shot.source_asset_ids) == 2:
            if not spec.supports_last_frame:
                out.append(RuleViolation("no_last_frame", f"{spec.model_id} lacks last frame", sid))
            a, b = (property_model.room_of(x) for x in shot.source_asset_ids)
            if (
                a is None
                or b is None
                or not property_model.are_adjacent(a.room_id, b.room_id, MIN_TRANSITION_CONFIDENCE)
            ):
                out.append(
                    RuleViolation("transition_not_adjacent", "transition rooms not adjacent", sid)
                )
    if plan.shots and plan.target_length_s > 0:
        ratio = plan.total_duration_s / plan.target_length_s
        if abs(ratio - 1) > LENGTH_TOLERANCE:
            out.append(
                RuleViolation(
                    "length_off_target",
                    f"total {plan.total_duration_s:.0f}s vs target {plan.target_length_s:.0f}s",
                    severity="warning",
                )
            )
    return out


def _check_shot_shape(shot_type: ShotType, assets: list[str], sid: str) -> list[RuleViolation]:
    if shot_type == ShotType.FRAME_TRANSITION and len(assets) != 2:
        return [RuleViolation("transition_needs_two", "transition needs 2 source images", sid)]
    if shot_type == ShotType.SINGLE_IMAGE and len(assets) != 1:
        return [RuleViolation("single_needs_one", "single_image needs 1 source image", sid)]
    return []


def repair_plan(
    plan: TourPlan,
    property_model: PropertyModel,
    analyses: ImageAnalysisSet,
    registry: ModelRegistry,
    index: AssetIndex,
    fallback_model: str,
    disclosure_text: str,
) -> TourPlan:
    fixed = plan.model_copy(deep=True)
    usable = {a.asset_id for a in index.usable_images()} & {a.asset_id for a in analyses.analyses}
    room_ids = {r.room_id for r in property_model.rooms}
    if not fixed.disclosure_text.strip():
        fixed.disclosure_text = disclosure_text
    kept = []
    for shot in fixed.shots:
        if shot.source_asset_ids[0] not in usable:
            continue
        shot.source_asset_ids = [a for a in shot.source_asset_ids if a in usable]
        if shot.model_id not in registry.models:
            shot.model_id = fallback_model
        if shot.room_id not in room_ids:
            room = property_model.room_of(shot.source_asset_ids[0])
            if room is None:
                continue
            shot.room_id = room.room_id
        spec = registry.get(shot.model_id)
        if shot.shot_type == ShotType.FRAME_TRANSITION:
            pair = [property_model.room_of(x) for x in shot.source_asset_ids]
            adjacent = (
                len(pair) == 2
                and pair[0] is not None
                and pair[1] is not None
                and property_model.are_adjacent(pair[0].room_id, pair[1].room_id)
            )
            if not (adjacent and spec.supports_last_frame):
                shot.shot_type = ShotType.SINGLE_IMAGE
        if shot.shot_type == ShotType.SINGLE_IMAGE:
            shot.source_asset_ids = shot.source_asset_ids[:1]
        limit = MAX_STRENGTH
        analysis = analyses.get(shot.source_asset_ids[0])
        if analysis.framing == Framing.DETAIL:
            limit = MAX_DETAIL_STRENGTH
        shot.camera_move = CameraMove(
            type=shot.camera_move.type, strength=min(shot.camera_move.strength, limit)
        )
        shot.duration_s = spec.clamp_duration(shot.duration_s)
        kept.append(shot)
    seen: set[str] = set()
    for n, shot in enumerate(kept, start=1):
        if shot.shot_id in seen:
            shot.shot_id = f"{shot.shot_id}_{n}"
        seen.add(shot.shot_id)
    fixed.shots = kept
    return fixed
