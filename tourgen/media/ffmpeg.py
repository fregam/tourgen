"""
FILE: tourgen/media/ffmpeg.py
ROLE: The only place that executes ffmpeg/ffprobe.

HOW IT WORKS:
  - run_ffmpeg(args) builds an argv list (never a shell string), logs the exact
    command, captures stderr and raises FFmpegError on failure.
  - probe(path) returns MediaInfo via ffprobe JSON.

CONNECTS TO:
  - media/kenburns.py, media/frames.py, media/compose.py, ingest/video_frames.py,
    generation/fake_client.py

INVARIANTS / GOTCHAS:
  - No shell=True anywhere. Text for drawtext goes through textfile=, not inline,
    to avoid escaping bugs with user-provided strings.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("tourgen.ffmpeg")


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str) -> None:
        super().__init__(f"ffmpeg failed: {shlex.join(cmd)}\n{stderr[-2000:]}")
        self.cmd = cmd
        self.stderr = stderr


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool


def run_ffmpeg(args: list[str], timeout_s: float = 1800) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    log.debug("run: %s", shlex.join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.stderr)


def probe(path: Path) -> MediaInfo:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.stderr)
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate") not in (None, "0/0"):
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else None
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return MediaInfo(
        duration_s=duration,
        width=int(video["width"]) if video else None,
        height=int(video["height"]) if video else None,
        fps=fps,
        has_video=video is not None,
        has_audio=audio is not None,
    )
