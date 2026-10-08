# 0004 — FakeLLM for offline runs

**Context:** BUILD_PROMPT asks for recorded Claude fixtures so tests and `make run-sample` run
offline. No API key is available in the build environment, so nothing could be recorded yet.
**Decision:** `llm/fake_llm.py` implements the LLM interface deterministically from filename
hints and prompt variables (and scriptable QA verdicts). `--fake-llm` selects it. Every real
call is logged to `analysis/_llm_log/` so real fixtures can be added later (PLAN 2.6).
**Consequences:** Offline runs test plumbing, not understanding quality. The evaluation set
(PLAN 7.2) must use real Claude calls.
