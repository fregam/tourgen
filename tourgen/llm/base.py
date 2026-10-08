"""
FILE: tourgen/llm/base.py
ROLE: The LLM interface every stage depends on (real: llm/claude.py, fake: llm/fake_llm.py).

HOW IT WORKS:
  - structured() renders a versioned prompt file with variables, attaches
    labelled images, and returns a validated Pydantic object of `schema`.

CONNECTS TO:
  - stages/understand.py, stages/plan.py, stages/qa.py

INVARIANTS / GOTCHAS:
  - `task` is a short tag ("analyze_image:<asset_id>") used for logs and cost lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ImageInput:
    label: str  # shown to the model right before the image, e.g. "asset_id=img_003"
    path: Path


class LLMClient(Protocol):
    def structured(
        self,
        *,
        task: str,
        model: str,
        prompt_name: str,
        variables: dict[str, str],
        images: list[ImageInput],
        schema: type[T],
        effort: str = "medium",
    ) -> T: ...


class LLMError(RuntimeError):
    pass
