"""
FILE: tourgen/generation/registry.py
ROLE: Loads config/models.yaml into typed ModelSpec objects (the model registry).

HOW IT WORKS:
  - load_registry() parses YAML → ModelRegistry; capabilities (frame fields,
    duration range, prompt limit, cost) are read by prompt builders, the
    planner prompt, core/rules.py and core/costs.py.

READS:  config/models.yaml
WRITES: nothing

CONNECTS TO:
  - generation/prompt_builders/*, core/rules.py, core/costs.py, stages/plan.py

INVARIANTS / GOTCHAS:
  - Endpoints and field names exist ONLY in models.yaml. Builders never hardcode them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ModelSpec(BaseModel):
    model_id: str
    family: str
    endpoint: str
    first_frame_field: str
    last_frame_field: str | None = None
    supports_last_frame: bool = False
    duration_field: str = "duration"
    duration_min_s: float
    duration_max_s: float
    integer_durations: bool = True
    resolutions: list[str] = []
    default_resolution: str | None = None
    resolution_field: str | None = None
    prompt_field: str = "prompt"
    prompt_max_chars: int
    negative_prompt_field: str | None = None
    usd_per_second: float = 0.0
    relative_cost: float = 1.0
    verified: bool = False
    notes: str = ""

    def clamp_duration(self, seconds: float) -> float:
        value = min(max(seconds, self.duration_min_s), self.duration_max_s)
        return float(round(value)) if self.integer_durations else value

    def duration_ok(self, seconds: float) -> bool:
        return self.duration_min_s <= seconds <= self.duration_max_s


class ModelRegistry(BaseModel):
    models: dict[str, ModelSpec] = Field(default_factory=dict)

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model '{model_id}'. Known: {sorted(self.models)}") from exc

    def transition_capable(self) -> list[ModelSpec]:
        return [m for m in self.models.values() if m.supports_last_frame]

    def capabilities_summary(self) -> str:
        """Compact text for the planner prompt."""
        lines = []
        for spec in self.models.values():
            lines.append(
                f"- {spec.model_id}: durations {spec.duration_min_s:g}-{spec.duration_max_s:g}s, "
                f"last_frame={'yes' if spec.supports_last_frame else 'no'}, "
                f"relative_cost={spec.relative_cost:g}. {spec.notes}"
            )
        return "\n".join(lines)


def load_registry(path: Path) -> ModelRegistry:
    raw = yaml.safe_load(path.read_text()) or {}
    models = {
        model_id: ModelSpec.model_validate({"model_id": model_id, **body})
        for model_id, body in (raw.get("models") or {}).items()
    }
    return ModelRegistry(models=models)
