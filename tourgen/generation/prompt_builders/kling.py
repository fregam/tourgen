"""
FILE: tourgen/generation/prompt_builders/kling.py
ROLE: Payload builder for the Kling family (kling-3.0, kling-3.0-turbo).

HOW IT WORKS:
  - Uses base.compose_prompt within the registry's prompt limit, then
    base.build_payload. Kling takes a negative prompt; Turbo has no last frame
    (registry says so, build_payload enforces it).

CONNECTS TO:
  - Registered in prompt_builders/__init__.py; called by stages/compile_prompts.py

INVARIANTS / GOTCHAS:
  - Multi-shot mode is deliberately unused in v1 (one camera move per shot).
"""

from __future__ import annotations

from tourgen.generation.prompt_builders.base import build_payload, compose_prompt
from tourgen.generation.registry import ModelSpec
from tourgen.models.analysis import ImageAnalysis
from tourgen.models.generation import GenerationPayload
from tourgen.models.plan import Shot


def compile_kling_payload(
    shot: Shot,
    spec: ModelSpec,
    attempt: int,
    analysis: ImageAnalysis | None,
    extra_preservation: list[str],
) -> GenerationPayload:
    prompt = compose_prompt(shot, analysis, spec.prompt_max_chars, extra_preservation)
    return build_payload(shot, spec, attempt, prompt)
