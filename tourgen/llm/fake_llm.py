"""
FILE: tourgen/llm/fake_llm.py
ROLE: Deterministic offline stand-in for llm/claude.py (tests, `--fake-llm`, `make run-sample`).

HOW IT WORKS:
  - Dispatches on the requested schema and builds a plausible answer from
    filename hints (e.g. "kitchen_2.jpg" → kitchen) and the prompt variables.
  - QA verdicts can be scripted per shot: FakeLLM(qa_script={"s03": ["retry", "pass"]}).

CONNECTS TO:
  - Implements llm/base.LLMClient; selected by core/context.py when options.fake_llm.

INVARIANTS / GOTCHAS:
  - Never used for real jobs: its "understanding" is filename-based only.
  - Output must pass core/rules.py, so it mirrors the planner's hard rules.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from tourgen.llm.base import ImageInput
from tourgen.models.analysis import (
    Adjacency,
    Framing,
    ImageAnalysis,
    PropertyModel,
    Room,
)
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, ShotType, TourPlan, Transition
from tourgen.models.qa import QAReport, RetryAdjustment, Verdict

T = TypeVar("T", bound=BaseModel)

# Canonical walk-through order; also the fake adjacency chain.
ROOM_ORDER = [
    "exterior_front", "entry", "hallway", "living_room", "dining_room", "kitchen",
    "terrace", "office", "bedroom", "bathroom", "laundry", "garage", "garden",
    "pool", "exterior_back", "view", "other",
]  # fmt: skip
_ALIASES = {
    "living": "living_room", "lounge": "living_room", "dining": "dining_room",
    "bath": "bathroom", "front": "exterior_front", "facade": "exterior_front",
    "exterior": "exterior_front", "back": "exterior_back", "yard": "garden",
    "hall": "hallway", "foyer": "entry", "study": "office", "balcony": "terrace",
}  # fmt: skip
_EXTERIOR = {"exterior_front", "exterior_back", "garden", "pool", "terrace", "view"}


def guess_room_type(name: str) -> str:
    tokens = re.split(r"[^a-z]+", name.lower())
    joined = "_".join(t for t in tokens if t)
    for room in ROOM_ORDER:
        if room in joined:
            return room
    for token in tokens:
        if token in _ALIASES:
            return _ALIASES[token]
    return "other"


class FakeLLM:
    def __init__(self, qa_script: dict[str, list[str]] | None = None) -> None:
        self.qa_script = qa_script or {}
        self.calls: list[str] = []

    def structured(
        self,
        *,
        task: str,
        model: str,
        prompt_name: str,
        variables: dict[str, str],
        images: list[ImageInput],
        schema: type[T],
        effort: str = "medium",
    ) -> T:
        self.calls.append(task)
        builders: dict[type[BaseModel], Any] = {
            ImageAnalysis: self._analysis,
            PropertyModel: self._property_model,
            TourPlan: self._plan,
            QAReport: self._qa,
        }
        return cast(T, builders[schema](variables))

    # --- per-schema answers ---------------------------------------------------
    def _analysis(self, v: dict[str, str]) -> ImageAnalysis:
        room = guess_room_type(v["original_name"])
        detail = "detail" in v["original_name"].lower()
        exterior = room in _EXTERIOR
        if detail:
            moves = [CameraMoveType.STATIC, CameraMoveType.DOLLY_IN]
        elif exterior:
            moves = [CameraMoveType.CRANE_UP, CameraMoveType.DOLLY_IN, CameraMoveType.TRUCK_RIGHT]
        else:
            moves = [CameraMoveType.DOLLY_IN, CameraMoveType.TRUCK_LEFT, CameraMoveType.PAN_RIGHT]
        return ImageAnalysis(
            asset_id=v["asset_id"],
            room_type=room,
            is_indoor=not exterior,
            features=[],
            framing=Framing.DETAIL if detail else Framing.WIDE,
            lighting="daylight",
            motion_suitability=0.3 if detail else 0.8,
            safe_camera_moves=moves,
            notes=f"fake analysis of {v['original_name']}",
        )

    def _property_model(self, v: dict[str, str]) -> PropertyModel:
        analyses = [ImageAnalysis.model_validate(a) for a in json.loads(v["analyses_json"])]
        rooms: dict[str, Room] = {}
        for a in analyses:
            room = rooms.setdefault(
                a.room_type,
                Room(
                    room_id=a.room_type,
                    room_type=a.room_type,
                    label=a.room_type.replace("_", " ").capitalize(),
                    asset_ids=[],
                    is_exterior=not a.is_indoor,
                ),
            )
            room.asset_ids.append(a.asset_id)
            if room.hero_asset_id is None and a.framing == Framing.WIDE:
                room.hero_asset_id = a.asset_id
        ordered = sorted(rooms.values(), key=lambda r: ROOM_ORDER.index(r.room_type))
        adjacencies = [
            Adjacency(room_a=a.room_id, room_b=b.room_id, via="opening", confidence=0.8)
            for a, b in itertools.pairwise(ordered)
        ]
        heroes = [r.hero_asset_id for r in ordered if r.hero_asset_id]
        return PropertyModel(
            rooms=ordered,
            adjacencies=adjacencies,
            hero_asset_ids=heroes[:1] + heroes[-1:],
            summary="Fake property model built from filenames.",
        )

    def _plan(self, v: dict[str, str]) -> TourPlan:
        pm = PropertyModel.model_validate_json(v["property_model_json"])
        analyses = {
            a["asset_id"]: ImageAnalysis.model_validate(a) for a in json.loads(v["analyses_json"])
        }
        max_shots = int(v["max_shots"])
        target = float(v["target_length"])
        shots: list[Shot] = []
        transition_done = False
        for index, room in enumerate(pm.rooms):
            for k, asset_id in enumerate(room.asset_ids[:2]):
                a = analyses[asset_id]
                if a.motion_suitability < 0.3:
                    continue
                move = a.safe_camera_moves[k % len(a.safe_camera_moves)]
                strength = 0.15 if a.framing == Framing.DETAIL else 0.4
                shots.append(self._shot(len(shots), room, [asset_id], move, strength, v, a))
            nxt = pm.rooms[index + 1] if index + 1 < len(pm.rooms) else None
            if (
                not transition_done
                and nxt is not None
                and not room.is_exterior
                and not nxt.is_exterior
                and room.hero_asset_id
                and nxt.hero_asset_id
            ):
                pair = [room.hero_asset_id, nxt.hero_asset_id]
                shot = self._shot(
                    len(shots), room, pair, CameraMoveType.DOLLY_IN, 0.4, v,
                    analyses[pair[0]], model=v["transition_model"],
                )  # fmt: skip
                shot.shot_type = ShotType.FRAME_TRANSITION
                shot.on_screen_text = None
                shots.append(shot)
                transition_done = True
        shots = shots[: max_shots - 1]
        if pm.hero_asset_ids:
            hero = pm.hero_asset_ids[0]
            room = pm.room_of(hero) or pm.rooms[0]
            shots.append(
                self._shot(
                    len(shots), room, [hero], CameraMoveType.DOLLY_IN, 0.3, v, analyses[hero]
                )
            )
            shots[-1].on_screen_text = None
        per_shot = max(3.0, min(6.0, target / max(len(shots), 1)))
        for shot in shots:
            shot.duration_s = float(round(per_shot))
        return TourPlan(
            title="Property tour",
            shots=shots,
            music_mood="warm",
            target_length_s=target,
            disclosure_text=v["disclosure_text"],
            language=v["language"],
            aspect_ratios=json.loads(v["aspect_ratios"]),
        )

    @staticmethod
    def _shot(
        n: int,
        room: Room,
        assets: list[str],
        move: CameraMoveType,
        strength: float,
        v: dict[str, str],
        a: ImageAnalysis,
        model: str | None = None,
    ) -> Shot:
        return Shot(
            shot_id=f"s{n + 1:02d}",
            room_id=room.room_id,
            source_asset_ids=assets,
            camera_move=CameraMove(type=move, strength=strength),
            duration_s=4.0,
            model_id=model or v["preferred_model"],
            purpose=f"Show the {room.label.lower()}",
            on_screen_text=room.label,
            transition_in=Transition.DISSOLVE,
            reframe_center_x=a.subject_center_x,
        )

    def _qa(self, v: dict[str, str]) -> QAReport:
        shot_id, attempt = v["shot_id"], int(v["attempt"])
        script = self.qa_script.get(shot_id, [])
        verdict = Verdict(script[attempt - 1]) if attempt <= len(script) else Verdict.PASS
        return QAReport(
            shot_id=shot_id,
            attempt=attempt,
            fidelity_score=0.92 if verdict == Verdict.PASS else 0.4,
            verdict=verdict,
            reasons=[] if verdict == Verdict.PASS else ["scripted failure"],
            adjustment=RetryAdjustment(
                reduce_motion=verdict == Verdict.RETRY, shorten_duration=verdict == Verdict.RETRY
            ),
        )
