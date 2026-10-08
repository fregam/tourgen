"""
FILE: tourgen/generation/prompt_builders/__init__.py
ROLE: Maps a registry `family` to its payload builder.

HOW IT WORKS:
  - builder_for(spec) returns the compile_<family>_payload function.

CONNECTS TO:
  - stages/compile_prompts.py; add a family here when adding a builder file.
"""

from __future__ import annotations

from collections.abc import Callable

from tourgen.generation.prompt_builders.kling import compile_kling_payload
from tourgen.generation.prompt_builders.seedance import compile_seedance_payload
from tourgen.generation.prompt_builders.wan import compile_wan_payload
from tourgen.generation.registry import ModelSpec
from tourgen.models.analysis import ImageAnalysis
from tourgen.models.generation import GenerationPayload
from tourgen.models.plan import Shot

PayloadBuilder = Callable[
    [Shot, ModelSpec, int, ImageAnalysis | None, list[str]], GenerationPayload
]

BUILDERS: dict[str, PayloadBuilder] = {
    "kling": compile_kling_payload,
    "seedance": compile_seedance_payload,
    "wan": compile_wan_payload,
}


def builder_for(spec: ModelSpec) -> PayloadBuilder:
    try:
        return BUILDERS[spec.family]
    except KeyError as exc:
        raise KeyError(f"No prompt builder for family '{spec.family}' ({spec.model_id})") from exc
