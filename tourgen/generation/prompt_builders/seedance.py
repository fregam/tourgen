"""
FILE: tourgen/generation/prompt_builders/seedance.py
ROLE: Payload builder for ByteDance Seedance 2.0.

HOW IT WORKS:
  - Seedance has no negative-prompt field (registry), so the "avoid" list is
    folded into the positive prompt as an explicit preservation clause.
  - Duration is required and always sent (build_payload clamps it to range).

CONNECTS TO:
  - Registered in prompt_builders/__init__.py; called by stages/compile_prompts.py
"""

from __future__ import annotations

from tourgen.generation.prompt_builders.base import build_payload, compose_prompt
from tourgen.generation.registry import ModelSpec
from tourgen.models.analysis import ImageAnalysis
from tourgen.models.generation import GenerationPayload
from tourgen.models.plan import Shot

AVOID_CLAUSE = "Avoid people, warped walls, bent lines, fast motion and camera shake"


def compile_seedance_payload(
    shot: Shot,
    spec: ModelSpec,
    attempt: int,
    analysis: ImageAnalysis | None,
    extra_preservation: list[str],
) -> GenerationPayload:
    clauses = [*extra_preservation, AVOID_CLAUSE]
    prompt = compose_prompt(shot, analysis, spec.prompt_max_chars, clauses)
    return build_payload(shot, spec, attempt, prompt)
