# PLAN — tourgen
Status legend: [ ] todo · [x] done · [~] in progress · [!] blocked (add reason)
Last updated: 2026-10-08 by initial build session

## Phase 0 — Foundations
- [ ] 0.1 Repo scaffold: pyproject (uv), ruff, mypy, pytest, Makefile, .env.example, .gitignore
- [ ] 0.2 Create overview.md skeleton and CLAUDE.md via /init, edit per BUILD_PROMPT §10
- [ ] 0.3 Verify Higgsfield docs (llms.txt + chosen model pages); write docs/higgsfield-notes.md
- [ ] 0.4 config.py settings + config/models.yaml registry + registry.py loader with tests
- [ ] 0.5 All Pydantic schemas in tourgen/models/ with round-trip tests
- [ ] 0.6 workspace.py + JobState + pipeline.py skeleton with resume/skip logic
- [ ] DoD: `make lint typecheck test` green; `tourgen new` creates a job workspace
