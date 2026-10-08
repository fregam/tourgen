"""
FILE: tourgen/media/quality.py
ROLE: Image quality metrics and perceptual hashing for stage 2 (normalize).

HOW IT WORKS:
  - compute_quality(): resolution, blur (Laplacian variance), exposure mean and
    clipped highlight/shadow percentages → AssetQuality with flags.
  - compute_phash() / phash_distance(): near-duplicate detection.

CONNECTS TO:
  - stages/normalize.py

INVARIANTS / GOTCHAS:
  - Thresholds are heuristics; flags inform the planner, they never delete assets.
  - Blur is measured on a fixed-width downscale so it is comparable across sizes.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

from tourgen.models.assets import AssetQuality, QualityFlag

MIN_LONG_SIDE_PX = 1024
BLUR_THRESHOLD = 60.0
UNDEREXPOSED_MEAN = 50.0
OVEREXPOSED_MEAN = 205.0
DUPLICATE_MAX_DISTANCE = 4


def compute_quality(path: Path) -> AssetQuality:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Unreadable image: {path}")
    height, width = image.shape[:2]
    scale = 1024 / max(width, 1)
    small = cv2.resize(image, (1024, max(1, int(height * scale)))) if scale < 1 else image
    blur = float(cv2.Laplacian(small, cv2.CV_64F).var())
    mean = float(np.mean(image))
    highlights = float(np.mean(image >= 250) * 100)
    shadows = float(np.mean(image <= 5) * 100)

    flags: list[QualityFlag] = []
    if max(width, height) < MIN_LONG_SIDE_PX:
        flags.append(QualityFlag.LOW_RESOLUTION)
    if blur < BLUR_THRESHOLD:
        flags.append(QualityFlag.BLURRY)
    if mean < UNDEREXPOSED_MEAN or shadows > 25:
        flags.append(QualityFlag.UNDEREXPOSED)
    if mean > OVEREXPOSED_MEAN or highlights > 25:
        flags.append(QualityFlag.OVEREXPOSED)
    return AssetQuality(
        width=width,
        height=height,
        blur_variance=round(blur, 2),
        exposure_mean=round(mean, 2),
        clipped_highlights_pct=round(highlights, 2),
        clipped_shadows_pct=round(shadows, 2),
        flags=flags,
    )


def compute_phash(path: Path) -> str:
    with Image.open(path) as img:
        return str(imagehash.phash(img))


def phash_distance(a: str, b: str) -> int:
    return int(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b))
