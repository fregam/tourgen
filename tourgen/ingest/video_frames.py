"""
FILE: tourgen/ingest/video_frames.py
ROLE: Turn an input video into still keyframes usable as photos.

HOW IT WORKS:
  1. probe() the video for duration.
  2. Scene-change frames via ffmpeg select='gt(scene,T)' (capped).
  3. Plus N evenly spaced frames so static walkthroughs still yield stills.
  Each frame becomes an Asset(kind=VIDEO_FRAME, parent_asset_id=<video>).

CONNECTS TO:
  - stages/ingest.py; uses media/ffmpeg.py

INVARIANTS / GOTCHAS:
  - The original video is kept as an asset too (possible B-roll).
  - Near-duplicate frames are removed later by normalize (phash), not here.
"""

from __future__ import annotations

from pathlib import Path

from tourgen.core.workspace import Workspace
from tourgen.media.ffmpeg import probe, run_ffmpeg
from tourgen.models.assets import Asset, AssetKind

SCENE_THRESHOLD = 0.3
MAX_SCENE_FRAMES = 12
EVEN_FRAMES = 4


def extract_keyframes(workspace: Workspace, video: Asset) -> list[Asset]:
    path = workspace.abs(video.input_path)
    duration = probe(path).duration_s
    out_dir = workspace.inputs_dir / f"{video.asset_id}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[tuple[Path, float | None]] = []
    scene_pattern = out_dir / "scene_%03d.jpg"
    if not any(out_dir.glob("scene_*.jpg")):
        run_ffmpeg(
            [
                "-i", str(path),
                "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',scale='min(2560,iw)':-2",
                "-vsync", "vfr", "-frames:v", str(MAX_SCENE_FRAMES), "-q:v", "2",
                str(scene_pattern),
            ]
        )  # fmt: skip
    frames += [(p, None) for p in sorted(out_dir.glob("scene_*.jpg"))]

    for index in range(EVEN_FRAMES):
        t = duration * (index + 0.5) / EVEN_FRAMES
        out = out_dir / f"even_{index:02d}.jpg"
        if not out.exists():
            run_ffmpeg(
                ["-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-q:v", "2", str(out)]
            )
        frames.append((out, t))

    assets = []
    for index, (frame, frame_time) in enumerate(frames):
        assets.append(
            Asset(
                asset_id=f"{video.asset_id}_f{index:02d}",
                kind=AssetKind.VIDEO_FRAME,
                source=video.source,
                original_name=f"{Path(video.original_name).stem}_{frame.stem}.jpg",
                input_path=workspace.rel(frame),
                parent_asset_id=video.asset_id,
                frame_time_s=frame_time,
            )
        )
    video.duration_s = duration
    return assets
