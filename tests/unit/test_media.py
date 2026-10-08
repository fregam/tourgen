"""media/*: quality metrics, phash, Ken Burns, frames, compose (small renders)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageFilter

from tourgen.media.compose import (
    JoinItem,
    finalize,
    join_clips,
    make_card,
    normalize_clip,
    spec_for_aspect,
)
from tourgen.media.ffmpeg import FFmpegError, probe, run_ffmpeg
from tourgen.media.frames import extract_frames, frame_times
from tourgen.media.kenburns import make_kenburns_clip, zoompan_expressions
from tourgen.media.quality import compute_phash, compute_quality, phash_distance
from tourgen.models.assets import QualityFlag
from tourgen.models.plan import CameraMoveType, Transition

PHOTO = Path(__file__).parents[1] / "fixtures" / "photos" / "kitchen.jpg"
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def test_quality_flags_blur_and_resolution(tmp_path: Path) -> None:
    sharp = compute_quality(PHOTO)
    assert QualityFlag.BLURRY not in sharp.flags
    blurred = tmp_path / "blur.jpg"
    Image.open(PHOTO).resize((640, 427)).filter(ImageFilter.GaussianBlur(8)).save(blurred)
    quality = compute_quality(blurred)
    assert {QualityFlag.BLURRY, QualityFlag.LOW_RESOLUTION} <= set(quality.flags)


def test_phash_detects_near_duplicates(tmp_path: Path) -> None:
    copy = tmp_path / "copy.jpg"
    Image.open(PHOTO).resize((900, 600)).save(copy, quality=60)
    other = PHOTO.parent / "bedroom.jpg"
    assert phash_distance(compute_phash(PHOTO), compute_phash(copy)) <= 4
    assert phash_distance(compute_phash(PHOTO), compute_phash(other)) > 10


def test_ffmpeg_errors_are_typed(tmp_path: Path) -> None:
    with pytest.raises(FFmpegError):
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "x.mp4")])


@pytest.mark.parametrize("move", list(CameraMoveType))
def test_zoompan_expressions_cover_every_move(move: CameraMoveType) -> None:
    z, x, y = zoompan_expressions(move, 0.4, 90)
    assert z and x and y


def test_kenburns_frames_and_compose(tmp_path: Path) -> None:
    clip = make_kenburns_clip(
        PHOTO, tmp_path / "kb.mp4", 2, CameraMoveType.TRUCK_RIGHT, 0.4, 320, 180, 15
    )
    info = probe(clip)
    assert (info.width, info.height) == (320, 180)
    assert info.duration_s == pytest.approx(2, abs=0.15)

    frames = extract_frames(clip, tmp_path / "frames", 4)
    assert len(frames) == 4 and all(f.exists() for f in frames)
    assert frame_times(2.0, 3) == [0.0, 0.95, 1.9]

    spec = spec_for_aspect("9:16", 15, FONT, scale=0.2)
    assert (spec.width, spec.height) == (216, 384)
    a = normalize_clip(clip, tmp_path / "a.mp4", spec, 2.5, center_x=0.2, label="Kitchen")
    card = make_card(tmp_path / "card.mp4", ["Title", "Agent"], 1.0, spec)
    total = join_clips(
        [JoinItem(card, 1.0, Transition.CUT), JoinItem(a, 2.5, Transition.DISSOLVE),
         JoinItem(a, 2.5, Transition.CUT)],
        tmp_path / "joined.mp4", 0.5,
    )  # fmt: skip
    assert total == pytest.approx(5.5)
    out = finalize(tmp_path / "joined.mp4", tmp_path / "final.mp4", spec, total, "AI disclosure")
    final = probe(out)
    assert final.has_audio and (final.width, final.height) == (216, 384)
    assert final.duration_s == pytest.approx(5.5, abs=0.2)
    with pytest.raises(ValueError):
        finalize(tmp_path / "joined.mp4", tmp_path / "x.mp4", spec, total, "  ")
