"""
FILE: tourgen/stages/understand.py
ROLE: Pipeline stage 3. Claude vision → ImageAnalysis per image, then one aggregate call
      → PropertyModel (rooms, adjacency, hero shots).

HOW IT WORKS:
  1. For each usable image without a cached analysis: prompts/analyze_image.md
     (parallel, small pool). Partial progress is saved, so a crash resumes.
  2. prompts/build_property_model.md with all analyses (+ floor plan image if any).
  3. Sanity: unknown asset ids from the model are dropped; unassigned images are
     put in their own room so nothing silently disappears.

READS:  assets/asset_index.json, assets/*.jpg
WRITES: analysis/image_analyses.json, analysis/property_model.json, analysis/_llm_log/

CONNECTS TO:
  - Called by core/pipeline.py; uses llm (ctx.llm), models/analysis.py
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from tourgen.core.context import StageContext, StageResult
from tourgen.llm.base import ImageInput
from tourgen.models.analysis import ImageAnalysis, ImageAnalysisSet, PropertyModel, Room
from tourgen.models.assets import Asset, AssetIndex
from tourgen.models.job import StageName

NAME = StageName.UNDERSTAND
ANALYSIS_WORKERS = 4


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    return (
        ws.read_model_or_none(ws.image_analyses_file, ImageAnalysisSet) is not None
        and ws.read_model_or_none(ws.property_model_file, PropertyModel) is not None
    )


def listing_context(index: AssetIndex) -> str:
    if index.listing is None:
        return "(none)"
    info = index.listing
    parts = [info.title, info.description]
    if info.rooms:
        parts.append(f"{info.rooms} rooms")
    if info.surface_m2:
        parts.append(f"{info.surface_m2:g} m²")
    return " | ".join(p for p in parts if p)[:2000] or "(none)"


def run(ctx: StageContext) -> StageResult:
    ws = ctx.workspace
    index = ws.read_model(ws.asset_index_file, AssetIndex)
    cached = ws.read_model_or_none(ws.image_analyses_file, ImageAnalysisSet) or ImageAnalysisSet()
    done = {a.asset_id for a in cached.analyses}
    usable = index.usable_images()
    todo = [a for a in usable if a.asset_id not in done]
    context = listing_context(index)
    lock = threading.Lock()

    def analyze(asset: Asset) -> None:
        assert asset.normalized_path is not None
        result = ctx.llm.structured(
            task=f"analyze_image:{asset.asset_id}",
            model=ctx.settings.analysis_model,
            prompt_name="analyze_image",
            variables={
                "asset_id": asset.asset_id,
                "original_name": asset.original_name,
                "listing_context": context,
            },
            images=[ImageInput(f"asset_id={asset.asset_id}", ws.abs(asset.normalized_path))],
            schema=ImageAnalysis,
            effort="low",
        )
        result.asset_id = asset.asset_id
        with lock:
            cached.analyses.append(result)
            ws.write_model(ws.image_analyses_file, cached)

    with ThreadPoolExecutor(max_workers=ANALYSIS_WORKERS) as pool:
        list(pool.map(analyze, todo))
    usable_ids = {a.asset_id for a in usable}
    cached.analyses = sorted(
        (a for a in cached.analyses if a.asset_id in usable_ids), key=lambda a: a.asset_id
    )
    ws.write_model(ws.image_analyses_file, cached)

    floor_plan = index.floor_plan()
    images = []
    note = "none provided"
    if floor_plan and floor_plan.normalized_path:
        images.append(ImageInput("FLOOR PLAN", ws.abs(floor_plan.normalized_path)))
        note = "provided as the image above; use it for room adjacency"
    model = ctx.llm.structured(
        task="build_property_model",
        model=ctx.settings.property_model_model,
        prompt_name="build_property_model",
        variables={
            "analyses_json": json.dumps([a.model_dump(mode="json") for a in cached.analyses]),
            "floor_plan_note": note,
            "listing_context": context,
        },
        images=images,
        schema=PropertyModel,
        effort="high",
    )
    model = sanitize_property_model(model, cached)
    ws.write_model(ws.property_model_file, model)
    return StageResult(
        f"{len(cached.analyses)} images analysed, {len(model.rooms)} rooms, "
        f"{len(model.adjacencies)} adjacencies"
    )


def sanitize_property_model(model: PropertyModel, analyses: ImageAnalysisSet) -> PropertyModel:
    known = {a.asset_id for a in analyses.analyses}
    assigned: set[str] = set()
    for room in model.rooms:
        room.asset_ids = [a for a in room.asset_ids if a in known and a not in assigned]
        assigned.update(room.asset_ids)
        if room.hero_asset_id not in room.asset_ids:
            room.hero_asset_id = room.asset_ids[0] if room.asset_ids else None
    model.rooms = [r for r in model.rooms if r.asset_ids]
    for analysis in analyses.analyses:
        if analysis.asset_id not in assigned:
            model.rooms.append(
                Room(
                    room_id=f"{analysis.room_type}_{analysis.asset_id}",
                    room_type=analysis.room_type,
                    label=analysis.room_type.replace("_", " ").capitalize(),
                    asset_ids=[analysis.asset_id],
                    hero_asset_id=analysis.asset_id,
                    is_exterior=not analysis.is_indoor,
                )
            )
    room_ids = {r.room_id for r in model.rooms}
    model.adjacencies = [
        a for a in model.adjacencies if a.room_a in room_ids and a.room_b in room_ids
    ]
    model.hero_asset_ids = [a for a in model.hero_asset_ids if a in known]
    return model
