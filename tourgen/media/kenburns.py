"""
FILE: tourgen/media/kenburns.py
ROLE: Local, zero-hallucination motion: pan/zoom over the real photo with ffmpeg zoompan.

HOW IT WORKS:
  - make_kenburns_clip() scales/crops the photo to the output aspect at 2x size
    (reduces zoompan jitter), then animates zoom/x/y per the CameraMove.
  - Moves that need real parallax (orbit, crane) degrade to a gentle dolly-in.

CONNECTS TO:
  - stages/qa.py (fallback for rejected shots), generation/fake_client.py
    (placeholder clips); uses media/ffmpeg.py

INVARIANTS / GOTCHAS:
  - Only ever crops/zooms the real pixels, so it cannot invent content.
"""

from __future__ import annotations

from pathlib import Path

from tourgen.media.ffmpeg import run_ffmpeg
from tourgen.models.plan import CameraMoveType


def zoompan_expressions(move: CameraMoveType, strength: float, frames: int) -> tuple[str, str, str]:
    """Return (z, x, y) zoompan expressions; `on` is the output frame number."""
    zmax = 1.0 + 0.25 * max(min(strength, 1.0), 0.05)
    n = max(frames - 1, 1)
    progress = f"(on/{n})"
    center_x, center_y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if move == CameraMoveType.DOLLY_OUT:
        return f"{zmax}-({zmax}-1)*{progress}", center_x, center_y
    if move in (CameraMoveType.TRUCK_LEFT, CameraMoveType.PAN_LEFT):
        return f"{zmax}", f"(iw-iw/zoom)*(1-{progress})", center_y
    if move in (CameraMoveType.TRUCK_RIGHT, CameraMoveType.PAN_RIGHT):
        return f"{zmax}", f"(iw-iw/zoom)*{progress}", center_y
    if move == CameraMoveType.TILT_UP:
        return f"{zmax}", center_x, f"(ih-ih/zoom)*(1-{progress})"
    if move in (CameraMoveType.TILT_DOWN, CameraMoveType.CRANE_UP):
        # Crane-up reveals downward motion in frame; approximate with a tilt.
        return f"{zmax}", center_x, f"(ih-ih/zoom)*{progress}"
    if move == CameraMoveType.STATIC:
        return "1.0", center_x, center_y
    # dolly_in, orbit_*: gentle push-in toward the centre.
    return f"1+({zmax}-1)*{progress}", center_x, center_y


def make_kenburns_clip(
    image: Path,
    out: Path,
    duration_s: float,
    move: CameraMoveType,
    strength: float,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> Path:
    frames = max(round(duration_s * fps), 1)
    z, x, y = zoompan_expressions(move, strength, frames)
    filters = (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        "format=yuv420p,setsar=1"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i", str(image), "-vf", filters, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out),
        ]
    )  # fmt: skip
    return out
