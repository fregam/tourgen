"""llm/prompts.py: every prompt file has a header/version and renders."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tourgen.llm.prompts import PROMPTS_DIR, load_prompt


@pytest.mark.parametrize("path", sorted(PROMPTS_DIR.glob("*.md")), ids=lambda p: p.stem)
def test_prompt_files_load_and_render(path: Path) -> None:
    template = load_prompt(path.stem)
    assert re.match(r"\d+\.\d+\.\d+", template.version)
    names = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", template.system + template.user))
    system, user = template.render({n: f"<{n}>" for n in names})
    assert "{{" not in system + user
    if names:
        with pytest.raises(KeyError):
            template.render({})


def test_expected_prompts_exist() -> None:
    assert {p.stem for p in PROMPTS_DIR.glob("*.md")} >= {
        "analyze_image", "build_property_model", "plan_tour", "qa_clip",
    }  # fmt: skip
