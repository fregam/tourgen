# 0001 — Python stack

**Context:** BUILD_PROMPT §2 fixes the stack; recorded here so changes need a new ADR.
**Decision:** Python 3.12, uv, Pydantic v2 schemas as the only cross-stage contract, Anthropic
SDK, httpx, Typer CLI, ffmpeg via subprocess, FastAPI for Phase 6, pytest/ruff/mypy (strict on
`tourgen/core` and `tourgen/models`).
**Consequences:** Everything crossing a boundary is a Pydantic model serialized in the job
workspace; stages can be re-run in isolation. selectolax ≥ 1.0 removed the Modest backend, so
we use `selectolax.lexbor`.
