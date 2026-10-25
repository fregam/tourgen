# PLAN — tourgen
Status legend: [ ] todo · [x] done · [~] in progress · [!] blocked (add reason)
Last updated: 2026-10-25 — added Phase 8 (listing URL → email delivery); real-API steps still open

## Phase 0 — Foundations
- [x] 0.1 Repo scaffold: pyproject (uv), ruff, mypy, pytest, Makefile, .env.example, .gitignore
- [x] 0.2 Create overview.md skeleton and CLAUDE.md via /init, edit per BUILD_PROMPT §10
  - note: CLAUDE.md written directly with the §10 content (the repo was empty, so /init had nothing to infer)
- [!] 0.3 Verify Higgsfield docs (llms.txt + chosen model pages); write docs/higgsfield-notes.md
  - [x] Core API verified from the official SDK source (higgsfield-client 0.2.0); notes written
  - [!] llms.txt + model pages: docs.higgsfield.ai is blocked by the build environment's egress proxy; model entries are `verified: false`
- [x] 0.4 config.py settings + config/models.yaml registry + registry.py loader with tests
- [x] 0.5 All Pydantic schemas in tourgen/models/ with round-trip tests
  - [x] added models/delivery.py (Manifest) — not in the original layout
- [x] 0.6 workspace.py + JobState + pipeline.py skeleton with resume/skip logic
- [x] DoD: `make lint typecheck test` green; `tourgen new` creates a job workspace

## Phase 1 — Ingest & Normalize
- [x] 1.1 Local folder ingest
- [x] 1.2 Video keyframe extraction
- [x] 1.3 Listing URL ingest (+ robots/rights reminder)
  - [ ] Test against a real listing site (only MockTransport-tested so far)
- [x] 1.4 Normalize: EXIF, format, resize, dedupe (phash), quality metrics
- [x] DoD: sample fixtures → assets/asset_index.json with quality flags

## Phase 2 — Understand & Plan (Claude)
- [x] 2.1 llm/claude.py wrapper (images, structured output, retry-on-validation-error, cost)
  - [x] refusal handling + server-side fallbacks (ADR 0003)
- [x] 2.2 analyze_image prompt + stage → ImageAnalysis per asset
- [x] 2.3 build_property_model prompt → PropertyModel (rooms, adjacency, hero shots)
- [x] 2.4 plan_tour prompt → TourPlan
- [x] 2.5 rules.py validation; planner re-asked with violations if invalid
  - [x] deterministic repair_plan() after re-asks
- [~] 2.6 Recorded-response fixtures so tests run offline
  - [x] FakeLLM (ADR 0004) makes the whole pipeline run offline
  - [ ] Record real Claude responses from analysis/_llm_log/ into tests/fixtures/llm/ (needs ANTHROPIC_API_KEY)
- [~] DoD: `tourgen plan <job>` produces a valid, rule-passing tour_plan.json on fixtures
  - [x] verified with --fake-llm
  - [ ] verify with real Claude

## Phase 3 — Compile & Generate (Higgsfield)
- [x] 3.1 Prompt builders per model family, constrained by registry
- [x] 3.2 fake_client.py
- [x] 3.3 higgsfield_client.py: presigned upload, idempotent submit, poll, download (httpx, ADR 0002)
- [x] 3.4 scheduler.py: semaphore, backoff+jitter, concurrency-error handling, persistence of request_ids
- [x] 3.5 `estimate`, `--dry-run`, `--budget` controls
- [ ] 3.6 One real end-to-end shot against Higgsfield (manual, cheap model) — record result in higgsfield-notes.md
  - needs HF_KEY + verified model entries (0.3); steps listed in docs/higgsfield-notes.md
- [~] DoD: fake run completes all shots; one real clip downloaded to clips/
  - [x] fake run completes all shots

## Phase 4 — QA loop
- [x] 4.1 Frame extraction for QA
- [x] 4.2 qa_clip prompt → QAReport (source vs generated)
- [x] 4.3 Retry policy: adjust payload from QA failure reasons; max_retries
- [x] 4.4 Ken Burns fallback for rejected shots
- [x] DoD: injected bad clip is caught and retried/fallen back (tests/integration/test_qa_loop.py)

## Phase 5 — Assemble & Deliver
- [x] 5.1 Clip normalization (fps, resolution, color)
- [x] 5.2 Transitions, titles, room labels, branding
- [x] 5.3 Music bed; optional TTS voiceover behind interface (off by default)
  - [ ] Ship or document a licensed default music track (assets/music/ is empty → silent bed)
- [x] 5.4 Mandatory AI disclosure overlay
- [x] 5.5 16:9 and 9:16 renders (planned reframing via reframe_center_x, ADR 0005)
- [x] 5.6 manifest.json (provenance) + report.md (costs, timings, QA summary)
- [x] DoD: `tourgen run <inputs>` produces final videos end-to-end with fake generation

## Phase 6 — Interface
- [x] 6.1 FastAPI: create job, upload, start, status (SSE), download
- [x] 6.2 Minimal UI: upload → review/edit TourPlan (reorder, drop shots) → approve cost → watch progress → download
- [ ] 6.3 Webhook receiver for Higgsfield completions (polling stays as recovery)
- [x] DoD: full flow from browser (verified with headless Chromium + fakes; API flow in tests/integration/test_api.py)

## Phase 7 — Hardening
- [~] 7.1 Structured logging + per-job log file
  - [x] per-job jobs/<id>/job.log
  - [ ] structured (JSON) log format
- [ ] 7.2 Prompt/version evaluation set (5+ sample properties, scored QA pass rate)
- [x] 7.3 README for humans

## Phase 8 — URL in, email out (ADR 0006)
- [x] 8.1 Stage 10 `notify`: SMTP email with size-fitted video attachments; outbox mode; never resends on resume
- [x] 8.2 `--email` on new/run, `tourgen send <job> --to …`
- [x] 8.3 Preflight checks: `tourgen doctor`, automatic at the start of `tourgen run`
- [x] 8.4 Web API/UI: create a job from a listing URL + email recipients
- [x] 8.5 Keep listing image filenames through ingest (room hints for Claude)
- [x] DoD (offline): `tourgen run --url <local fake listing> --email … --email-outbox --fake-*` → .eml with both videos (tests/integration/test_url_to_inbox.py)
- [ ] DoD (real): one real listing URL → email received in a real inbox
  - needs: ANTHROPIC_API_KEY, HF_KEY, TOURGEN_SMTP_* in .env; network access to the listing site, api.higgsfield.ai and the SMTP server; Phase 0.3/3.6 done
