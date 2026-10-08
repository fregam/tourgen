"""
FILE: tourgen/ingest/local.py
ROLE: Ingest local photos/videos (folder or explicit files) into inputs/.

HOW IT WORKS:
  - collect_local_files() expands folders (sorted, non-recursive into hidden dirs)
    and classifies by extension.
  - copy_into_inputs() copies each file under a stable asset id and returns Asset rows.

CONNECTS TO:
  - stages/ingest.py; videos are then handled by ingest/video_frames.py

INVARIANTS / GOTCHAS:
  - Originals are copied, never moved or modified.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tourgen.core.workspace import Workspace
from tourgen.models.assets import Asset, AssetKind

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def classify(path: Path) -> AssetKind | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return AssetKind.PHOTO
    if ext in VIDEO_EXTS:
        return AssetKind.VIDEO
    return None


def collect_local_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and not any(part.startswith(".") for part in p.parts)
            )
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)
    return [f for f in files if classify(f) is not None]


def copy_into_inputs(
    workspace: Workspace,
    file: Path,
    asset_id: str,
    kind: AssetKind | None = None,
    source: str | None = None,
) -> Asset:
    kind = kind or classify(file)
    if kind is None:
        raise ValueError(f"Unsupported file type: {file}")
    dest = workspace.inputs_dir / f"{asset_id}{file.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(file, dest)
    return Asset(
        asset_id=asset_id,
        kind=kind,
        source=source or str(file.resolve()),
        original_name=file.name,
        input_path=workspace.rel(dest),
    )
