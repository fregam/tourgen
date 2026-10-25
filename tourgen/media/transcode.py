"""
FILE: tourgen/media/transcode.py
ROLE: Make an email-sized copy of a final render (stage 10).

HOW IT WORKS:
  - fit_to_size() picks a video bitrate from the byte budget and duration, scales
    to 720p-class when the bitrate is low, encodes once, and re-encodes once at a
    lower bitrate if the result is still too big. Returns None when the video
    cannot fit at a watchable bitrate (the caller skips that attachment).

CONNECTS TO:
  - stages/notify.py; uses media/ffmpeg.py

INVARIANTS / GOTCHAS:
  - Never touches the master render; writes a separate file.
"""

from __future__ import annotations

from pathlib import Path

from tourgen.media.ffmpeg import probe, run_ffmpeg

AUDIO_KBPS = 96
MIN_VIDEO_KBPS = 250  # below this the tour is not worth sending
SCALE_DOWN_BELOW_KBPS = 2500
SAFETY = 0.9  # container overhead + rate-control overshoot


def _target_size(width: int, height: int, video_kbps: float) -> tuple[int, int]:
    if video_kbps >= SCALE_DOWN_BELOW_KBPS or max(width, height) <= 1280:
        return width, height
    factor = 1280 / max(width, height)
    return int(width * factor) // 2 * 2, int(height * factor) // 2 * 2


def fit_to_size(src: Path, dst: Path, max_bytes: int) -> Path | None:
    info = probe(src)
    duration = max(info.duration_s, 0.1)
    if src.stat().st_size <= max_bytes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return dst
    video_kbps = max_bytes * 8 * SAFETY / duration / 1000 - AUDIO_KBPS
    for _ in range(2):
        if video_kbps < MIN_VIDEO_KBPS:
            return None
        width, height = _target_size(info.width or 1920, info.height or 1080, video_kbps)
        kbps = int(video_kbps)
        dst.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            [
                "-i", str(src), "-vf", f"scale={width}:{height}",
                "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
                "-maxrate", f"{int(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
                "-movflags", "+faststart", str(dst),
            ]
        )  # fmt: skip
        if dst.stat().st_size <= max_bytes:
            return dst
        video_kbps *= 0.75
    return None
