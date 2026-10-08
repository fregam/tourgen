"""
FILE: tourgen/generation/prompt_builders/wan.py
ROLE: Payload builder for the Wan family (image_url + optional end_image_url).

HOW IT WORKS:
  - Same wording as base; Wan supports a negative prompt (registry).

CONNECTS TO:
  - Registered in prompt_builders/__init__.py; called by stages/compile_prompts.py
"""

from __future__ import annotations

from tourgen.generation.prompt_builders.base import build_payload, compose_prompt
from tourgen.generation.registry import ModelSpec
from tourgen.models.analysis import ImageAnalysis
from tourgen.models.generation import GenerationPayload
from tourgen.models.plan import Shot


def compile_wan_payload(
    shot: Shot,
    spec: ModelSpec,
    attempt: int,
    analysis: ImageAnalysis | None,
    extra_preservation: list[str],
) -> GenerationPayload:
    prompt = compose_prompt(shot, analysis, spec.prompt_max_chars, extra_preservation)
    return build_payload(shot, spec, attempt, prompt)
