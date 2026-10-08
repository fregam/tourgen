"""
FILE: tourgen/llm/claude.py
ROLE: The single Claude client wrapper. Every Claude call in tourgen goes through here.

HOW IT WORKS:
  1. Renders a prompt file (llm/prompts.py) and attaches images, resized to
     settings.claude_max_image_px and base64-encoded as JPEG.
  2. Calls messages.parse() with the Pydantic schema as output_format (structured
     outputs), opting into server-side refusal fallbacks.
  3. On a validation failure, re-asks once with the validation error appended.
  4. Logs request metadata + response JSON to analysis/_llm_log/ and reports
     token cost through the on_cost callback (→ JobState ledger).

READS:  tourgen/llm/prompts/*.md, image files
WRITES: analysis/_llm_log/<n>_<task>.json

CONNECTS TO:
  - Implements llm/base.LLMClient; built by core/context.py
  - Prices from config.Settings.claude_prices

INVARIANTS / GOTCHAS:
  - SDK retries 429/5xx/connection errors itself (max_retries); we only retry
    on schema validation failure.
  - Check stop_reason == "refusal" before reading content.
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from PIL import Image
from pydantic import BaseModel, ValidationError

from tourgen.config import Settings
from tourgen.core.workspace import write_text_atomic
from tourgen.llm.base import ImageInput, LLMError
from tourgen.llm.prompts import load_prompt

T = TypeVar("T", bound=BaseModel)
CostCallback = Callable[[str, str, float], None]

# Server-side refusal fallback ("default" routing). See docs/decisions/0003-claude-client.md.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_TOKENS = 16000


def encode_image(path: Path, max_px: int) -> dict[str, Any]:
    with Image.open(path) as source:
        img = source.convert("RGB")
    img.thumbnail((max_px, max_px))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=88)
    data = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


class ClaudeClient:
    def __init__(
        self,
        settings: Settings,
        log_dir: Path | None = None,
        on_cost: CostCallback | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.settings = settings
        self.log_dir = log_dir
        self.on_cost = on_cost
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key, max_retries=4
        )
        self._counter = itertools.count(1)

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
    ) -> T:
        template = load_prompt(prompt_name)
        system, user_text = template.render(variables)
        content: list[dict[str, Any]] = []
        for image in images:
            content.append({"type": "text", "text": image.label})
            content.append(encode_image(image.path, self.settings.claude_max_image_px))
        content.append({"type": "text", "text": user_text})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]

        last_error: str | None = None
        for attempt in (1, 2):
            started = time.monotonic()
            response = self._client.messages.parse(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_format=schema,
                output_config={"effort": effort},  # type: ignore[arg-type]
                extra_headers={"anthropic-beta": _FALLBACK_BETA},
                extra_body={"fallbacks": "default"},
            )
            self._account(task, model, response)
            raw_text = "".join(
                getattr(block, "text", "") for block in response.content if block.type == "text"
            )
            self._log(task, template.name, template.version, model, attempt, response, raw_text)
            if response.stop_reason == "refusal":
                raise LLMError(f"{task}: Claude declined the request ({response.stop_details})")
            if response.stop_reason == "max_tokens":
                last_error = "response truncated at max_tokens"
            else:
                try:
                    parsed = response.parsed_output
                    if parsed is not None:
                        return parsed
                    return schema.model_validate_json(raw_text)
                except ValidationError as exc:
                    last_error = str(exc)
            # Re-ask once with the validation problem (BUILD_PROMPT §4.2).
            messages = [
                *messages,
                {"role": "assistant", "content": raw_text or "(empty)"},
                {
                    "role": "user",
                    "content": "Your previous answer failed validation:\n"
                    f"{last_error}\nReturn a corrected answer that matches the schema exactly.",
                },
            ]
            self._log_note(
                task, f"attempt {attempt} invalid after {time.monotonic() - started:.1f}s"
            )
        raise LLMError(f"{task}: invalid structured output after retry: {last_error}")

    # --- accounting & logging ---------------------------------------------
    def _account(self, task: str, model: str, response: Any) -> None:
        price = self.settings.claude_prices.get(model)
        usage = response.usage
        if price is None or usage is None:
            return
        input_tokens = (usage.input_tokens or 0) + (
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )
        usd = (
            input_tokens * price.input_per_mtok + (usage.output_tokens or 0) * price.output_per_mtok
        ) / 1_000_000
        if self.on_cost is not None:
            self.on_cost("claude", f"{task} ({model})", usd)

    def _log(
        self,
        task: str,
        prompt_name: str,
        version: str,
        model: str,
        attempt: int,
        response: Any,
        raw_text: str,
    ) -> None:
        if self.log_dir is None:
            return
        record = {
            "task": task,
            "prompt": prompt_name,
            "prompt_version": version,
            "model": model,
            "served_model": getattr(response, "model", None),
            "attempt": attempt,
            "stop_reason": response.stop_reason,
            "usage": response.usage.model_dump() if response.usage else None,
            "response_text": raw_text,
        }
        safe_task = task.replace("/", "_").replace(":", "_")
        path = self.log_dir / f"{next(self._counter):04d}_{safe_task}.json"
        write_text_atomic(path, json.dumps(record, indent=2, default=str))

    def _log_note(self, task: str, note: str) -> None:
        if self.log_dir is not None:
            path = self.log_dir / f"{next(self._counter):04d}_note.json"
            write_text_atomic(path, json.dumps({"task": task, "note": note}))
