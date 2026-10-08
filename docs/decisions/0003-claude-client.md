# 0003 — Claude client: structured outputs + refusal fallback

**Context:** Every LLM answer must parse into a Pydantic schema; safety classifiers on current
models can decline (`stop_reason == "refusal"`).
**Decision:** `llm/claude.py` uses `messages.parse(output_format=Schema)` with
`output_config.effort` per task (low for per-image analysis, high for planning), opts into
server-side fallbacks (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`), checks
`stop_reason` before reading content, and re-asks once on validation failure.
**Consequences:** Schemas double as output schemas, so they avoid unions/free-form dicts.
Model IDs live only in `config.Settings`. Requires an `anthropic` SDK with `messages.parse`.
