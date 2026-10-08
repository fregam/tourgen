"""
FILE: tourgen/stages/normalize.py
ROLE: Pipeline stage 2. EXIF-orient, convert to JPEG, cap resolution, compute quality
      metrics and perceptual hashes, mark near-duplicates.

HOW IT WORKS:
  1. Each image-like asset → assets/<asset_id>.jpg (RGB, long side ≤ max_image_side_px).
  2. media/quality.py metrics + flags; phash for dedupe.
  3. Photos are considered before video frames, so a duplicate frame never
     displaces a real photo.

READS:  assets/asset_index.json, inputs/*
WRITES: assets/<asset_id>.jpg, assets/asset_index.json (updated)

CONNECTS TO:
  - Called by core/pipeline.py; uses media/quality.py

INVARIANTS / GOTCHAS:
  - Low-quality images are flagged, never deleted. Unreadable files are logged
    and left without normalized_path (so they are not "usable").
"""

from __future__ import annotations

from PIL import Image, ImageOps, UnidentifiedImageError

from tourgen.core.context import StageContext, StageResult
from tourgen.media.quality import (
    DUPLICATE_MAX_DISTANCE,
    compute_phash,
    compute_quality,
    phash_distance,
)
from tourgen.models.assets import AssetIndex, AssetKind, QualityFlag
from tourgen.models.job import StageName

NAME = StageName.NORMALIZE
_IMAGE_KINDS = (AssetKind.PHOTO, AssetKind.VIDEO_FRAME, AssetKind.FLOOR_PLAN)


def is_complete(ctx: StageContext) -> bool:
    ws = ctx.workspace
    index = ws.read_model_or_none(ws.asset_index_file, AssetIndex)
    if index is None:
        return False
    return all(
        a.normalized_path is not None and ws.abs(a.normalized_path).exists()
        for a in index.assets
        if a.kind in _IMAGE_KINDS and a.quality is not None
    ) and any(a.quality is not None for a in index.assets)


def run(ctx: StageContext) -> StageResult:
    ws = ctx.workspace
    index = ws.read_model(ws.asset_index_file, AssetIndex)
    max_side = ctx.settings.max_image_side_px
    failed = 0
    for asset in index.assets:
        if asset.kind not in _IMAGE_KINDS:
            continue
        dest = ws.assets_dir / f"{asset.asset_id}.jpg"
        try:
            with Image.open(ws.abs(asset.input_path)) as source:
                img = ImageOps.exif_transpose(source).convert("RGB")
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                img.save(dest, format="JPEG", quality=92)
        except (UnidentifiedImageError, OSError) as exc:
            ctx.logger.warning("cannot read %s: %s", asset.original_name, exc)
            failed += 1
            continue
        asset.normalized_path = ws.rel(dest)
        asset.quality = compute_quality(dest)
        asset.phash = compute_phash(dest)
        asset.duplicate_of = None

    kept: list[tuple[str, str]] = []
    duplicates = 0
    candidates = sorted(
        (a for a in index.assets if a.kind in (AssetKind.PHOTO, AssetKind.VIDEO_FRAME) and a.phash),
        key=lambda a: 0 if a.kind == AssetKind.PHOTO else 1,
    )
    for asset in candidates:
        assert asset.phash is not None
        match = next(
            (aid for aid, h in kept if phash_distance(asset.phash, h) <= DUPLICATE_MAX_DISTANCE),
            None,
        )
        if match is not None:
            asset.duplicate_of = match
            if asset.quality and QualityFlag.DUPLICATE not in asset.quality.flags:
                asset.quality.flags.append(QualityFlag.DUPLICATE)
            duplicates += 1
        else:
            kept.append((asset.asset_id, asset.phash))

    ws.write_model(ws.asset_index_file, index)
    flagged = sum(1 for a in index.assets if a.quality and a.quality.flags)
    return StageResult(
        f"{len(index.usable_images())} usable images, {duplicates} duplicates, "
        f"{flagged} flagged, {failed} unreadable"
    )
