"""
FILE: tourgen/models/plan.py
ROLE: Schemas for the tour plan (stage 4): ordered shots, camera moves, narration.

HOW IT WORKS:
  - TourPlan is the structured output of prompts/plan_tour.md and the input to
    core/rules.py validation, prompt compilation and assembly.

READS/WRITES: plan/tour_plan.json

CONNECTS TO:
  - Produced by stages/plan.py; validated by core/rules.py
  - Read by stages/compile_prompts.py, stages/assemble.py, stages/deliver.py

INVARIANTS / GOTCHAS:
  - One camera move per shot (BUILD_PROMPT §5). `strength` is 0–1, slow by default.
  - FRAME_TRANSITION shots have exactly two source assets (first, last).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CameraMoveType(StrEnum):
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    TRUCK_LEFT = "truck_left"
    TRUCK_RIGHT = "truck_right"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ORBIT_LEFT = "orbit_left"
    ORBIT_RIGHT = "orbit_right"
    CRANE_UP = "crane_up"
    STATIC = "static"


class ShotType(StrEnum):
    SINGLE_IMAGE = "single_image"
    FRAME_TRANSITION = "frame_transition"


class Transition(StrEnum):
    CUT = "cut"
    DISSOLVE = "dissolve"
    FADE = "fade"


class CameraMove(BaseModel):
    type: CameraMoveType
    strength: float = Field(default=0.4, ge=0.0, le=1.0, description="0 = barely moves, 1 = max")


class NarrationLine(BaseModel):
    text: str
    language: str = "en"


class Shot(BaseModel):
    shot_id: str
    room_id: str
    shot_type: ShotType = ShotType.SINGLE_IMAGE
    source_asset_ids: list[str] = Field(min_length=1, max_length=2)
    camera_move: CameraMove
    duration_s: float = Field(gt=0)
    model_id: str = Field(description="Key in config/models.yaml")
    purpose: str = Field(description="Narrative purpose of this shot")
    on_screen_text: str | None = None
    narration: NarrationLine | None = None
    transition_in: Transition = Transition.DISSOLVE
    reframe_center_x: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Crop centre for the 9:16 render"
    )


class TourPlan(BaseModel):
    title: str
    shots: list[Shot]
    music_mood: str = "warm"
    target_length_s: float
    disclosure_text: str
    language: str = "en"
    aspect_ratios: list[str] = ["16:9", "9:16"]

    def get(self, shot_id: str) -> Shot:
        for shot in self.shots:
            if shot.shot_id == shot_id:
                return shot
        raise KeyError(shot_id)

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.shots)
