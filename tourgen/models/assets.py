"""
FILE: tourgen/models/assets.py
ROLE: Schemas for ingested and normalized media (stages 1–2).

HOW IT WORKS:
  - Asset: one input item (photo, video, extracted frame, floor plan).
  - AssetQuality: metrics computed by media/quality.py; flags never delete assets.
  - AssetIndex: the list persisted at assets/asset_index.json.

READS/WRITES: serialized by stages/ingest.py and stages/normalize.py

CONNECTS TO:
  - Produced by ingest/*, stages/normalize.py; read by every later stage.

INVARIANTS / GOTCHAS:
  - Paths are stored relative to the job root so a workspace can be moved.
  - Duplicates keep `duplicate_of`; they are excluded downstream, not deleted.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AssetKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    VIDEO_FRAME = "video_frame"
    FLOOR_PLAN = "floor_plan"


class QualityFlag(StrEnum):
    LOW_RESOLUTION = "low_resolution"
    BLURRY = "blurry"
    UNDEREXPOSED = "underexposed"
    OVEREXPOSED = "overexposed"
    DUPLICATE = "duplicate"


class AssetQuality(BaseModel):
    width: int
    height: int
    blur_variance: float = Field(description="Laplacian variance; low = blurry")
    exposure_mean: float = Field(description="Mean luma 0–255")
    clipped_highlights_pct: float
    clipped_shadows_pct: float
    flags: list[QualityFlag] = []


class Asset(BaseModel):
    asset_id: str
    kind: AssetKind
    source: str = Field(description="Original path or URL the asset came from")
    original_name: str
    input_path: str = Field(description="Raw copy under inputs/, relative to job root")
    normalized_path: str | None = Field(
        default=None, description="Normalized image under assets/, relative to job root"
    )
    parent_asset_id: str | None = Field(
        default=None, description="Video an extracted frame came from"
    )
    frame_time_s: float | None = None
    phash: str | None = None
    quality: AssetQuality | None = None
    duplicate_of: str | None = None
    duration_s: float | None = Field(default=None, description="Videos only")

    @property
    def usable_image(self) -> bool:
        return (
            self.kind in (AssetKind.PHOTO, AssetKind.VIDEO_FRAME)
            and self.normalized_path is not None
            and self.duplicate_of is None
        )


class ListingInfo(BaseModel):
    """Metadata scraped from a listing page (ingest/listing_url.py)."""

    url: str
    title: str | None = None
    description: str | None = None
    rooms: int | None = None
    surface_m2: float | None = None
    image_urls: list[str] = []


class AssetIndex(BaseModel):
    assets: list[Asset] = []
    listing: ListingInfo | None = None

    def get(self, asset_id: str) -> Asset:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(asset_id)

    def usable_images(self) -> list[Asset]:
        return [a for a in self.assets if a.usable_image]

    def floor_plan(self) -> Asset | None:
        return next((a for a in self.assets if a.kind == AssetKind.FLOOR_PLAN), None)
