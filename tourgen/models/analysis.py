"""
FILE: tourgen/models/analysis.py
ROLE: Schemas for Claude's understanding of the property (stage 3).

HOW IT WORKS:
  - ImageAnalysis: per-image structured output of prompts/analyze_image.md.
  - PropertyModel: aggregate output of prompts/build_property_model.md
    (rooms, which images belong where, adjacency graph, hero shots).

READS/WRITES: analysis/image_analyses.json, analysis/property_model.json

CONNECTS TO:
  - Produced by stages/understand.py via llm/claude.py
  - Read by stages/plan.py, core/rules.py, stages/compile_prompts.py

INVARIANTS / GOTCHAS:
  - These models double as Claude structured-output schemas, so keep fields
    simple (no unions of objects, no free-form dicts).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from tourgen.models.plan import CameraMoveType


class Framing(StrEnum):
    WIDE = "wide"
    MEDIUM = "medium"
    DETAIL = "detail"


class OpeningKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    PASSAGE = "passage"
    STAIRS = "stairs"


class Opening(BaseModel):
    kind: OpeningKind
    position: str = Field(description="left | center | right | background")
    leads_to: str | None = Field(default=None, description="Room type it leads to, if inferable")


class ImageAnalysis(BaseModel):
    asset_id: str
    room_type: str = Field(description="e.g. exterior_front, living_room, kitchen, bedroom")
    is_indoor: bool
    features: list[str] = Field(description="Notable features: fireplace, island, view…")
    framing: Framing
    openings: list[Opening] = []
    lighting: str = Field(description="e.g. bright daylight, warm evening, mixed")
    defects: list[str] = Field(default=[], description="Clutter, lens distortion, people…")
    motion_suitability: float = Field(ge=0.0, le=1.0)
    safe_camera_moves: list[CameraMoveType]
    subject_center_x: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Horizontal centre of interest, for 9:16 reframing"
    )
    notes: str = ""


class ImageAnalysisSet(BaseModel):
    analyses: list[ImageAnalysis] = []

    def get(self, asset_id: str) -> ImageAnalysis:
        for analysis in self.analyses:
            if analysis.asset_id == asset_id:
                return analysis
        raise KeyError(asset_id)


class Room(BaseModel):
    room_id: str
    room_type: str
    label: str = Field(description="Human label for on-screen text, e.g. 'Kitchen'")
    asset_ids: list[str]
    hero_asset_id: str | None = None
    is_exterior: bool = False


class Adjacency(BaseModel):
    room_a: str
    room_b: str
    via: str = Field(default="unknown", description="door | opening | stairs | unknown")
    confidence: float = Field(ge=0.0, le=1.0)


class PropertyModel(BaseModel):
    rooms: list[Room]
    adjacencies: list[Adjacency] = []
    hero_asset_ids: list[str] = []
    summary: str = ""

    def room_of(self, asset_id: str) -> Room | None:
        return next((r for r in self.rooms if asset_id in r.asset_ids), None)

    def are_adjacent(self, room_a: str, room_b: str, min_confidence: float = 0.5) -> bool:
        if room_a == room_b:
            return True
        return any(
            {adj.room_a, adj.room_b} == {room_a, room_b} and adj.confidence >= min_confidence
            for adj in self.adjacencies
        )
