"""
FILE: tourgen/media/compose.py
ROLE: Final-cut building blocks: normalize clips, title/end cards, transitions, audio, disclosure.

HOW IT WORKS:
  1. normalize_clip(): scale-to-cover + crop to the target aspect around a planned
     centre (planned 9:16 reframing), fixed fps/timebase/pixel format, exact
     duration (pads by holding the last frame), optional room label.
  2. make_card(): solid-colour card with centred text lines (title / end card).
  3. join_clips(): one filter_complex chaining xfade (dissolve/fade) or concat (cut).
  4. finalize(): burns the mandatory AI disclosure over the whole video, adds the
     music bed (or silence) and optional voiceover segments.

CONNECTS TO:
  - stages/assemble.py; uses media/ffmpeg.py

INVARIANTS / GOTCHAS:
  - Every clip entering join_clips() must come from normalize_clip()/make_card()
    with identical size/fps/timebase, or xfade fails.
  - Text goes through textfile= (see media/ffmpeg.py note).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tourgen.media.ffmpeg import run_ffmpeg
from tourgen.models.plan import Transition

_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


@dataclass(frozen=True)
class RenderSpec:
    width: int
    height: int
    fps: int
    font: Path


def spec_for_aspect(aspect: str, fps: int, font: Path, scale: float = 1.0) -> RenderSpec:
    sizes = {"9:16": (1080, 1920), "1:1": (1080, 1080)}
    width, height = sizes.get(aspect, (1920, 1080))

    def even(value: float) -> int:  # libx264 + yuv420p need even dimensions
        return max(2, int(value * scale) // 2 * 2)

    return RenderSpec(even(width), even(height), fps, font)


def _drawtext(spec: RenderSpec, textfile: Path, size: int, x: str, y: str, box: bool) -> str:
    parts = [
        f"fontfile='{spec.font}'",
        f"textfile='{textfile}'",
        "fontcolor=white",
        f"fontsize={size}",
        f"x={x}",
        f"y={y}",
    ]
    if box:
        parts += ["box=1", "boxcolor=black@0.45", "boxborderw=18"]
    return "drawtext=" + ":".join(parts)


def normalize_clip(
    src: Path,
    dst: Path,
    spec: RenderSpec,
    duration_s: float,
    center_x: float = 0.5,
    label: str | None = None,
) -> Path:
    w, h = spec.width, spec.height
    filters = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}:x='(iw-{w})*{center_x:.3f}':y='(ih-{h})/2'",
        f"fps={spec.fps}",
        "setsar=1",
        f"tpad=stop_mode=clone:stop_duration={duration_s:.3f}",
        f"trim=duration={duration_s:.3f}",
        "setpts=PTS-STARTPTS",
        "settb=AVTB",
        "format=yuv420p",
    ]
    if label:
        textfile = dst.with_suffix(".label.txt")
        textfile.write_text(label)
        size = max(h // 22, 28)
        filters.insert(-1, _drawtext(spec, textfile, size, "60", f"h-{size * 3}", box=True))
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-vf", ",".join(filters), "-an", *_X264, str(dst)])
    return dst


def make_card(dst: Path, lines: list[str], duration_s: float, spec: RenderSpec) -> Path:
    w, h = spec.width, spec.height
    filters: list[str] = []
    size = max(min(w, h) // 14, 36)
    total = len(lines)
    for index, line in enumerate(lines):
        textfile = dst.with_name(f"{dst.stem}.line{index}.txt")
        textfile.write_text(line)
        fs = size if index == 0 else int(size * 0.6)
        y = f"(h/2)+{int((index - (total - 1) / 2) * size * 1.4)}-text_h/2"
        filters.append(_drawtext(spec, textfile, fs, "(w-text_w)/2", y, box=False))
    filters += ["fade=t=in:st=0:d=0.4", "settb=AVTB", "format=yuv420p"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=0x111111:s={w}x{h}:r={spec.fps}:d={duration_s:.3f}",
            "-vf", ",".join(filters), *_X264, str(dst),
        ]
    )  # fmt: skip
    return dst


@dataclass(frozen=True)
class JoinItem:
    path: Path
    duration_s: float
    transition_in: Transition


def join_clips(items: list[JoinItem], dst: Path, transition_s: float) -> float:
    """Join normalized clips; returns the resulting duration."""
    if not items:
        raise ValueError("join_clips needs at least one clip")
    inputs: list[str] = []
    for item in items:
        inputs += ["-i", str(item.path)]
    graph: list[str] = []
    current = "[0:v]"
    total = items[0].duration_s
    for index, item in enumerate(items[1:], start=1):
        label = f"[v{index}]"
        overlap = min(transition_s, item.duration_s / 2, total / 2)
        if item.transition_in == Transition.CUT or overlap <= 0.05:
            graph.append(f"{current}[{index}:v]concat=n=2:v=1:a=0{label}")
            total += item.duration_s
        else:
            kind = "fadeblack" if item.transition_in == Transition.FADE else "fade"
            graph.append(
                f"{current}[{index}:v]xfade=transition={kind}:duration={overlap:.3f}"
                f":offset={total - overlap:.3f}{label}"
            )
            total += item.duration_s - overlap
        current = label
    args = [*inputs]
    if graph:
        args += ["-filter_complex", ";".join(graph), "-map", current]
    args += [*_X264, "-an", str(dst)]
    run_ffmpeg(args)
    return total


@dataclass(frozen=True)
class VoiceSegment:
    path: Path
    start_s: float


def finalize(
    video: Path,
    dst: Path,
    spec: RenderSpec,
    duration_s: float,
    disclosure_text: str,
    music: Path | None = None,
    voice: list[VoiceSegment] | None = None,
) -> Path:
    if not disclosure_text.strip():
        raise ValueError("The AI disclosure line is mandatory")
    textfile = dst.with_suffix(".disclosure.txt")
    textfile.write_text(disclosure_text)
    size = max(spec.height // 48, 18)
    vf = _drawtext(spec, textfile, size, "(w-text_w)/2", f"h-{size * 2}", box=True)

    args = ["-i", str(video)]
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    voice = voice or []
    for segment in voice:
        args += ["-i", str(segment.path)]

    fade_out_start = max(duration_s - 2.0, 0.0)
    audio = [
        f"[1:a]atrim=duration={duration_s:.3f},volume={0.35 if voice else 0.8},"
        f"afade=t=in:d=1,afade=t=out:st={fade_out_start:.3f}:d=2[bed]"
    ]
    mix_inputs = "[bed]"
    for index, segment in enumerate(voice, start=2):
        delay = int(segment.start_s * 1000)
        audio.append(f"[{index}:a]adelay={delay}|{delay}[vo{index}]")
        mix_inputs += f"[vo{index}]"
    if voice:
        audio.append(f"{mix_inputs}amix=inputs={len(voice) + 1}:normalize=0[aout]")
    else:
        audio.append("[bed]anull[aout]")
    graph = f"[0:v]{vf}[vout];" + ";".join(audio)
    args += [
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        *_X264, "-c:a", "aac", "-b:a", "160k", "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    run_ffmpeg(args)
    return dst
