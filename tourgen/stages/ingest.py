"""
FILE: tourgen/stages/ingest.py
ROLE: Pipeline stage 1. Collects raw assets (local files, listing URLs, floor plan) into inputs/.

HOW IT WORKS:
  1. Local paths → ingest/local.py (photos img_NNN, videos vid_NNN).
  2. Videos → ingest/video_frames.py keyframes (vid_NNN_fMM).
  3. Listing URLs → ingest/listing_url.py (robots check, rights reminder, image download).
  4. Optional floor plan → Asset(kind=FLOOR_PLAN, id "floorplan").

READS:  job.json inputs
WRITES: inputs/*, assets/asset_index.json (normalized fields still empty)

CONNECTS TO:
  - Called by core/pipeline.py; next stage is stages/normalize.py

INVARIANTS / GOTCHAS:
  - Asset ids are assigned in input order, so re-running on the same inputs is stable.
"""

from __future__ import annotations

from pathlib import Path

from tourgen.core.context import StageContext, StageResult
from tourgen.ingest.listing_url import RIGHTS_REMINDER, download_images, fetch_listing
from tourgen.ingest.local import classify, collect_local_files, copy_into_inputs
from tourgen.ingest.video_frames import extract_keyframes
from tourgen.models.assets import AssetIndex, AssetKind
from tourgen.models.job import StageName

NAME = StageName.INGEST


def is_complete(ctx: StageContext) -> bool:
    index = ctx.workspace.read_model_or_none(ctx.workspace.asset_index_file, AssetIndex)
    return index is not None and bool(index.assets)


def run(ctx: StageContext) -> StageResult:
    ws, inputs = ctx.workspace, ctx.job.inputs
    index = AssetIndex()
    photo_n = video_n = 0

    for file in collect_local_files([Path(p) for p in inputs.paths]):
        if classify(file) == AssetKind.PHOTO:
            photo_n += 1
            asset_id = f"img_{photo_n:03d}"
        else:
            video_n += 1
            asset_id = f"vid_{video_n:03d}"
        asset = copy_into_inputs(ws, file, asset_id)
        index.assets.append(asset)
        if asset.kind == AssetKind.VIDEO:
            index.assets += extract_keyframes(ws, asset)

    for url in inputs.urls:
        ctx.logger.warning(RIGHTS_REMINDER)
        listing = fetch_listing(url)
        index.listing = listing
        downloaded = download_images(listing.image_urls, ws.inputs_dir / "_listing")
        for image_url, path in downloaded:
            photo_n += 1
            index.assets.append(
                copy_into_inputs(ws, path, f"img_{photo_n:03d}", AssetKind.PHOTO, source=image_url)
            )

    if inputs.floor_plan:
        index.assets.append(
            copy_into_inputs(ws, Path(inputs.floor_plan), "floorplan", AssetKind.FLOOR_PLAN)
        )

    if not index.assets:
        raise ValueError("No usable inputs found (photos, videos or listing images)")
    ws.write_model(ws.asset_index_file, index)
    return StageResult(f"{photo_n} photos, {video_n} videos, {len(index.assets)} assets total")
