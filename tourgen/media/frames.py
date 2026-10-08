"""
FILE: tourgen/media/frames.py
ROLE: Extract still frames from a clip (for QA in stage 7).

HOW IT WORKS:
  - extract_frames(clip, out_dir, count): first, last and evenly spaced frames
    between them, saved as frame_00.jpg … in time order.

CONNECTS TO:
  - stages/qa.py; uses media/ffmpeg.py

INVARIANTS / GOTCHAS:
  - The "last" frame is taken slightly before the end; seeking exactly to the
    duration often returns nothing.
"""

from __future__ import annotations

from pathlib import Path

from tourgen.media.ffmpeg import probe, run_ffmpeg


def frame_times(duration_s: float, count: int) -> list[float]:
    count = max(count, 2)
    end = max(duration_s - 0.1, 0.0)
    return [round(end * i / (count - 1), 3) for i in range(count)]


def extract_frames(clip: Path, out_dir: Path, count: int = 5) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe(clip).duration_s
    paths: list[Path] = []
    for index, t in enumerate(frame_times(duration, count)):
        out = out_dir / f"frame_{index:02d}.jpg"
        if not out.exists():
            run_ffmpeg(
                ["-ss", f"{t:.3f}", "-i", str(clip), "-frames:v", "1", "-q:v", "3", str(out)]
            )
        paths.append(out)
    return paths
