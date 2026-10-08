"""
FILE: tourgen/llm/prompts.py
ROLE: Loads and renders the versioned prompt files in tourgen/llm/prompts/.

HOW IT WORKS:
  - A prompt file = an HTML comment header (purpose, inputs, output schema,
    version) followed by "## system" and "## user" sections.
  - {{name}} placeholders are substituted; a missing variable is an error.

READS: tourgen/llm/prompts/*.md

CONNECTS TO:
  - llm/claude.py (rendering), tests/unit/test_prompts.py

INVARIANTS / GOTCHAS:
  - Bump `version:` in the header whenever the prompt text changes; the version
    is written into every LLM log record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
_HEADER_RE = re.compile(r"^<!--(.*?)-->", re.DOTALL)
_VERSION_RE = re.compile(r"^\s*version:\s*(\S+)", re.MULTILINE)
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str

    def render(self, variables: dict[str, str]) -> tuple[str, str]:
        return _fill(self.system, variables, self.name), _fill(self.user, variables, self.name)


def _fill(text: str, variables: dict[str, str], name: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt '{name}' needs variable '{key}'")
        return variables[key]

    return _VAR_RE.sub(replace, text)


@cache
def load_prompt(name: str) -> PromptTemplate:
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    header = _HEADER_RE.match(text)
    if header is None:
        raise ValueError(f"Prompt '{name}' is missing its header comment block")
    version = _VERSION_RE.search(header.group(1))
    if version is None:
        raise ValueError(f"Prompt '{name}' header has no version")
    body = text[header.end() :]
    parts = re.split(r"^## (system|user)\s*$", body, flags=re.MULTILINE)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"Prompt '{name}' needs '## system' and '## user' sections")
    return PromptTemplate(name, version.group(1), sections["system"], sections["user"])
