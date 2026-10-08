"""
FILE: tourgen/stages/assemble.py
ROLE: Pipeline stage 8. Builds the final cut for every requested aspect ratio.

HOW IT WORKS (per aspect ratio, via media/compose.py):
  1. Title card → each shot's outcome clip normalized to the render size, cropped
     around shot.reframe_center_x (planned 9:16 reframing), with its room label.
  2. End card (agent branding when the brief has it).
  3. join_clips() with the planned transitions; finalize() burns the mandatory AI
     disclosure, adds the music bed (assets/music/<mood>.*) or silence, and
     optional voiceover from the TTS provider (NullTTS by default → none).

READS:  plan/tour_plan.json, qa/outcomes.json, clips/
WRITES: render/final_<WxH>.mp4, render/_work/*

CONNECTS TO:
  - Called by core/pipeline.py; uses media/compose.py, media/tts.py

INVARIANTS / GOTCHAS:
  - Every render carries the disclosure line (finalize() refuses empty text).
"""

from __future__ import annotations

from pathlib import Path

from tourgen.core.context import StageContext, StageResult
from tourgen.media.compose import (
    JoinItem,
    RenderSpec,
    VoiceSegment,
    finalize,
    join_clips,
    make_card,
    normalize_clip,
    spec_for_aspect,
)
from tourgen.media.tts import NullTTS, TTSProvider
from tourgen.models.job import StageName
from tourgen.models.plan import TourPlan, Transition
from tourgen.models.qa import ShotOutcomeIndex

NAME = StageName.ASSEMBLE
_MUSIC_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")


def is_complete(ctx: StageContext) -> bool:
    plan = ctx.workspace.read_model_or_none(ctx.workspace.tour_plan_file, TourPlan)
    return plan is not None and all(
        ctx.workspace.final_render_file(a).exists() for a in plan.aspect_ratios
    )


def find_music(music_dir: Path, mood: str) -> Path | None:
    if not music_dir.is_dir():
        return None
    tracks = sorted(p for p in music_dir.iterdir() if p.suffix.lower() in _MUSIC_EXTS)
    preferred = [p for p in tracks if p.stem.lower().startswith(mood.lower())]
    candidates = preferred or tracks
    return candidates[0] if candidates else None


def run(ctx: StageContext, tts: TTSProvider | None = None) -> StageResult:
    ws = ctx.workspace
    plan = ws.read_model(ws.tour_plan_file, TourPlan)
    outcomes = ws.read_model(ws.outcomes_file, ShotOutcomeIndex)
    tts = tts or NullTTS()
    music = find_music(ctx.settings.music_dir, plan.music_mood)
    rendered = []
    for aspect in plan.aspect_ratios:
        spec = spec_for_aspect(
            aspect, ctx.defaults.fps, ctx.settings.font_path, ctx.settings.render_scale
        )
        work = ws.work_dir / aspect.replace(":", "x")
        work.mkdir(parents=True, exist_ok=True)
        items, voice = _build_items(ctx, plan, outcomes, spec, work, tts)
        joined = work / "joined.mp4"
        duration = join_clips(items, joined, ctx.defaults.transition_duration_s)
        finalize(
            joined, ws.final_render_file(aspect), spec, duration, plan.disclosure_text, music, voice
        )
        rendered.append(f"{aspect} ({duration:.0f}s)")
    note = "" if music else " — no music found in assets/music, silent bed"
    return StageResult("rendered " + ", ".join(rendered) + note)


def _build_items(
    ctx: StageContext,
    plan: TourPlan,
    outcomes: ShotOutcomeIndex,
    spec: RenderSpec,
    work: Path,
    tts: TTSProvider,
) -> tuple[list[JoinItem], list[VoiceSegment]]:
    ws = ctx.workspace
    brief = ctx.job.inputs.brief
    items: list[JoinItem] = []
    voice: list[VoiceSegment] = []
    title_lines = [plan.title] + ([brief.agent_name] if brief.agent_name else [])
    title = make_card(work / "title.mp4", title_lines, ctx.defaults.title_card_s, spec)
    items.append(JoinItem(title, ctx.defaults.title_card_s, Transition.CUT))
    elapsed = ctx.defaults.title_card_s
    overlap = ctx.defaults.transition_duration_s
    for shot in plan.shots:
        outcome = outcomes.get(shot.shot_id)
        if outcome is None:
            raise ValueError(f"No QA outcome for shot {shot.shot_id}")
        clip = normalize_clip(
            ws.abs(outcome.clip_path),
            work / f"{shot.shot_id}.mp4",
            spec,
            shot.duration_s,
            shot.reframe_center_x,
            shot.on_screen_text,
        )
        items.append(JoinItem(clip, shot.duration_s, shot.transition_in))
        start = elapsed - (0 if shot.transition_in == Transition.CUT else overlap)
        if shot.narration:
            audio = tts.synthesize(
                shot.narration.text, shot.narration.language, work / f"{shot.shot_id}.vo.wav"
            )
            if audio is not None:
                voice.append(VoiceSegment(audio, max(start, 0.0)))
        elapsed = start + shot.duration_s
    end_lines = [brief.agent_name or plan.title]
    if brief.agent_contact:
        end_lines.append(brief.agent_contact)
    end = make_card(work / "end.mp4", end_lines, ctx.defaults.end_card_s, spec)
    items.append(JoinItem(end, ctx.defaults.end_card_s, Transition.FADE))
    return items, voice
