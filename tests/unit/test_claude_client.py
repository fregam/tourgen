"""llm/claude.py with a stubbed Anthropic client (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tourgen.config import Settings
from tourgen.llm.base import ImageInput, LLMError
from tourgen.llm.claude import ClaudeClient
from tourgen.models.qa import QAReport

PHOTO = Path(__file__).parents[1] / "fixtures" / "photos" / "kitchen.jpg"
VALID = {"shot_id": "s01", "attempt": 1, "fidelity_score": 0.9, "verdict": "pass"}


def _response(text: str, parsed: Any = None, stop: str = "end_turn") -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
        model_dump=lambda: {"input_tokens": 1000, "output_tokens": 500},
    )  # fmt: skip
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        parsed_output=parsed,
        stop_reason=stop,
        stop_details=None,
        usage=usage,
        model="claude-sonnet-5-5",
    )


class StubMessages:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _client(
    tmp_path: Path, responses: list[SimpleNamespace]
) -> tuple[ClaudeClient, StubMessages, list[float]]:
    stub = StubMessages(responses)
    costs: list[float] = []
    client = ClaudeClient(
        Settings(anthropic_api_key="test"),
        log_dir=tmp_path,
        on_cost=lambda provider, desc, usd: costs.append(usd),
        client=SimpleNamespace(messages=stub),  # type: ignore[arg-type]
    )
    return client, stub, costs


def _call(client: ClaudeClient) -> QAReport:
    return client.structured(
        task="qa_clip:s01:1", model="claude-sonnet-5-5", prompt_name="qa_clip",
        variables={"shot_json": "{}", "shot_id": "s01", "attempt": "1", "max_attempts": "3",
                   "model_ids": "kling-3.0"},
        images=[ImageInput("SOURCE 1", PHOTO)], schema=QAReport,
    )  # fmt: skip


def test_structured_success_logs_and_costs(tmp_path: Path) -> None:
    report = QAReport.model_validate(VALID)
    client, stub, costs = _client(tmp_path, [_response(json.dumps(VALID), report)])
    assert _call(client) == report
    call = stub.calls[0]
    assert call["output_format"] is QAReport
    assert call["extra_body"] == {"fallbacks": "default"}
    content = call["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "SOURCE 1"}
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert costs == [pytest.approx((1000 * 2 + 500 * 10) / 1e6)]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_reasks_once_on_validation_error(tmp_path: Path) -> None:
    client, stub, _ = _client(
        tmp_path, [_response('{"verdict": "maybe"}'), _response(json.dumps(VALID))]
    )
    assert _call(client).verdict == "pass"
    second = stub.calls[1]["messages"]
    assert second[-1]["role"] == "user" and "failed validation" in second[-1]["content"]


def test_refusal_raises(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, [_response("", stop="refusal")])
    with pytest.raises(LLMError):
        _call(client)
