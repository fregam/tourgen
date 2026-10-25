# overview — tourgen

## What this is (3–5 lines)
`tourgen` turns whatever exists about a property (photos, videos, a listing URL, an optional
floor plan, a short brief) into a cinematic house-tour video in 16:9 and 9:16.
Claude understands the photos, plans the tour, writes model prompts and checks every clip;
Higgsfield animates the camera; ffmpeg assembles. Guiding rule: **animate the camera, never
alter the house.** Every output carries an AI disclosure line and a provenance manifest.
One command can go from a listing URL to the videos in someone's inbox (`--url … --email …`).

## Run it (the 5 commands that matter)
```bash
make setup                                   # uv sync (Python 3.12, ffmpeg must be on PATH)
make lint typecheck test                     # offline; no paid calls
make run-sample                              # full pipeline on fixtures with fake Claude + fake Higgsfield
tourgen run ./photos --job-id house1 --dry-run   # real Claude, stop before Higgsfield (writes payloads)
tourgen estimate house1 && tourgen generate house1 && tourgen render house1   # paid part
tourgen run --url https://listing.example/123 --email me@example.com --yes --budget 20  # URL → inbox
```
Also: `tourgen doctor [--url …] [--email …]` (what's missing before a run), `tourgen send <job> --to …`
(re-send the email), `tourgen status <job>`, `tourgen serve` (web UI at http://127.0.0.1:8000).

## Pipeline at a glance
```
INPUTS ─► 1 Ingest ─► 2 Normalize ─► 3 Understand ─► 4 Plan ─► 5 Compile prompts
        ─► 6 Generate ─► 7 QA (loop back to 5/6 on failure) ─► 8 Assemble ─► 9 Deliver ─► 10 Notify
```
(`tourgen run` first runs preflight checks — `core/preflight.py` — and refuses to start if a run would fail midway.)
1. **Ingest** — copy local files, extract video keyframes, scrape listing URL (robots.txt), floor plan.
2. **Normalize** — EXIF orient, JPEG, cap size, quality metrics + flags, phash dedupe.
3. **Understand** — Claude: one `ImageAnalysis` per image, then one `PropertyModel` (rooms, adjacency, heroes).
4. **Plan** — Claude: `TourPlan`; `core/rules.py` validates, Claude is re-asked, then mechanical repair.
5. **Compile** — per-model builders turn each `Shot` into a `GenerationPayload` (registry-driven).
6. **Generate** — budget gate, then upload/submit/poll/download with idempotency + bounded concurrency.
7. **QA** — Claude compares frames vs source photos → pass / retry (calmer payload) / reject → Ken Burns fallback.
8. **Assemble** — normalize clips, title/end cards, labels, transitions, music, disclosure, 16:9 + 9:16.
9. **Deliver** — `manifest.json` (provenance) + `report.md` (cost, timings, QA).
10. **Notify** — email the videos (size-fitted copies) to `--email` recipients over SMTP; no-op without recipients.

## Data flow
All state lives in `jobs/<job_id>/`; paths are owned by `core/workspace.py`.

| Stage | Reads | Writes | File |
|---|---|---|---|
| 1 Ingest | `job.json` inputs | `inputs/*`, `assets/asset_index.json` (`AssetIndex`) | `stages/ingest.py` |
| 2 Normalize | `AssetIndex`, `inputs/*` | `assets/<id>.jpg`, `AssetIndex` (quality, phash, duplicate_of) | `stages/normalize.py` |
| 3 Understand | `AssetIndex`, `assets/*.jpg` | `analysis/image_analyses.json` (`ImageAnalysisSet`), `analysis/property_model.json` (`PropertyModel`), `analysis/_llm_log/` | `stages/understand.py` |
| 4 Plan | analyses, `PropertyModel`, `AssetIndex`, brief | `plan/tour_plan.json` (`TourPlan`), `plan/validation.txt` | `stages/plan.py` |
| 5 Compile | `TourPlan`, analyses | `plan/payloads/<shot>__attempt1.json` (`GenerationPayload`) | `stages/compile_prompts.py` |
| 6 Generate | `TourPlan`, payloads | `clips/<shot>__attempt1.{mp4,record.json}` (`GenerationRecord`), ledger | `stages/generate.py` |
| 7 QA | clips, `TourPlan`, `assets/` | `qa/<shot>__attemptN.qa.json` (`QAReport`), `qa/frames/`, `qa/outcomes.json` (`ShotOutcomeIndex`), retry payloads/clips, `clips/<shot>__kenburns.mp4` | `stages/qa.py` |
| 8 Assemble | `TourPlan`, outcomes, clips | `render/final_16x9.mp4`, `render/final_9x16.mp4`, `render/_work/` | `stages/assemble.py` |
| 9 Deliver | job, plan, outcomes, `AssetIndex` | `render/manifest.json` (`Manifest`), `render/report.md` | `stages/deliver.py` |
| 10 Notify | `Manifest`, renders, `AssetIndex` (listing title), job inputs | `render/email/*.mp4`, `render/email/outbox/*.eml` (outbox mode), `render/notify.json` (`NotifyRecord`) | `stages/notify.py` |

## Code map
**Entry points**
- `tourgen/cli.py` — Typer commands (new, ingest, analyze, plan, estimate, generate, qa, render, send, run, doctor, status, serve) → `core/pipeline.py`.
- `tourgen/cli_options.py` — shared flag declarations + `build_job_spec()` (flags → `JobInputs`/`JobOptions`, email validation).
- `tourgen/cli_runner.py` — console logging, progress lines, cost-confirmation/budget loop around `run_pipeline`.
- `tourgen/api/app.py` — FastAPI: create job from uploads and/or listing URL (+ email recipients), plan, edit plan, approve cost, SSE progress, download. `api/static/index.html` — the single-page UI.
- `tourgen/config.py` — `Settings` (env/.env: keys, Claude model IDs + prices, concurrency, budgets, render_scale) and `TourDefaults` (config/defaults.yaml).

**Schemas — `tourgen/models/` (the contract between stages; no logic beyond validators)**
- `assets.py` — `Asset`, `AssetQuality`, `QualityFlag`, `ListingInfo`, `AssetIndex`.
- `analysis.py` — `ImageAnalysis`, `ImageAnalysisSet`, `Room`, `Adjacency`, `PropertyModel`.
- `plan.py` — `TourPlan`, `Shot`, `CameraMove(Type)`, `ShotType`, `Transition`, `NarrationLine`.
- `generation.py` — `GenerationPayload`, `GenerationRecord`, `RequestStatus`.
- `qa.py` — `QAReport` (structural hallucination ⇒ never PASS), `RetryAdjustment`, `ShotOutcome(Index)`.
- `job.py` — `JobState`, `StageName`/`STAGE_ORDER`, `StageStatus`, `Brief`, `JobInputs`, `JobOptions`, `CostLedger`.
- `delivery.py` — `Manifest`, `ManifestClip`, `NotifyRecord`, `EmailAttachment`.

**Core — `tourgen/core/`**
- `pipeline.py` — orchestrator: stage order, skip/resume, downstream reset, dry-run stop, job log. Uses every `stages/*`.
- `context.py` — `StageContext` (workspace, settings, job, registry, lazily built LLM/video clients — fake or real) and `StageResult`.
- `workspace.py` — the only module that knows the job folder layout; atomic typed JSON IO.
- `rules.py` — deterministic `validate_plan` / `repair_plan` (BUILD_PROMPT §5 + registry).
- `costs.py` — `estimate_job`, `check_budget` (`BudgetExceeded`, `ConfirmationRequired`).
- `preflight.py` — `run_checks` (tools, keys, network to Claude/Higgsfield/listing/SMTP) for `doctor` and `run`.

**Stages — `tourgen/stages/`** (each exposes `NAME`, `run(ctx) -> StageResult`, `is_complete(ctx)`)
- `ingest.py`, `normalize.py`, `understand.py`, `plan.py`, `compile_prompts.py` (also `compile_shot()` for retries), `generate.py`, `qa.py` (retry loop + fallback), `assemble.py`, `deliver.py`, `notify.py` — see table above.

**Ingest — `tourgen/ingest/`**
- `local.py` — collect/classify local files, copy into `inputs/`.
- `video_frames.py` — scene-change + evenly spaced keyframes from videos.
- `listing_url.py` — robots.txt check, HTML parse (og, srcset, JSON-LD), Playwright fallback, image download.

**LLM — `tourgen/llm/`**
- `base.py` — `LLMClient` protocol, `ImageInput`, `LLMError`.
- `claude.py` — the only Claude caller: image encoding, structured output, re-ask on validation error, refusal handling, cost + logs.
- `prompts.py` — loads/renders versioned prompt files.
- `prompts/` — `analyze_image.md`, `build_property_model.md`, `plan_tour.md`, `qa_clip.md` (header with version).
- `fake_llm.py` — deterministic offline stand-in (filename hints, scriptable QA verdicts).

**Generation — `tourgen/generation/`**
- `registry.py` — loads `config/models.yaml` into `ModelSpec`/`ModelRegistry`.
- `base.py` — `VideoClient` protocol, `SubmitResult`, `StatusResult`, error types.
- `higgsfield_client.py` — the only Higgsfield caller (httpx; upload, idempotent submit, status, download).
- `fake_client.py` — `FakeHiggsfieldClient` (Ken Burns clips, scripted failures/NSFW/concurrency/bad clips).
- `scheduler.py` — semaphore-bounded submit/poll/download, backoff + jitter, persisted records, resume.
- `prompt_builders/` — `base.py` (wording, preservation, field mapping, retry adjustments), `kling.py`, `seedance.py`, `wan.py`, `__init__.py` (family → builder).

**Media — `tourgen/media/`**
- `ffmpeg.py` — the only ffmpeg/ffprobe runner (argv lists, typed `FFmpegError`, `probe`).
- `quality.py` — blur/exposure/resolution metrics, phash.
- `frames.py` — frame extraction for QA.
- `kenburns.py` — local zero-hallucination pan/zoom (fallback + fake clips).
- `compose.py` — normalize/reframe clips, cards, transitions, disclosure, audio.
- `tts.py` — `TTSProvider` interface, `NullTTS` (voiceover off by default).
- `transcode.py` — `fit_to_size`: email-sized copy of a render within a byte budget.

**Notify — `tourgen/notify/`**
- `email.py` — the only SMTP caller: `build_tour_email`, `SmtpEmailSender` (STARTTLS 587 / TLS 465), `OutboxEmailSender` (.eml), `sender_for`.

**Other**
- `config/models.yaml` — Higgsfield model registry (endpoints, fields, durations, limits, prices; `verified` flags).
- `config/defaults.yaml` — default lengths, transitions, disclosure text, aspect ratios, preferred models.
- `assets/music/` — drop licensed tracks here (`<mood>*.mp3`).
- `scripts/make_fixtures.py` — generates the synthetic fixture photos.
- `tests/` — `conftest.py` (offline settings, fakes), `unit/`, `integration/` (pipeline, QA loop, scheduler, ingest, API).
- `docs/higgsfield-notes.md` (verified API facts), `docs/decisions/` (ADRs), `docs/BUILD_PROMPT.md` (original spec).

## Key concepts & invariants
- **Fidelity:** prompts only describe camera motion + light + preservation clauses; never add/remove/restyle. QA treats any structural change as non-passing; rejected shots fall back to Ken Burns over the real photo.
- **Workspace is the state.** Stages talk only via `tourgen/models` objects on disk. `job.json` + `is_complete()` drive skip/resume; running a stage resets later stages to pending.
- **Idempotency / no duplicate charges.** Payload files are never overwritten; a `GenerationRecord` is persisted the moment a `request_id` exists; resume polls existing requests; `Idempotency-Key = sha256(job:shot:attempt)`.
- **Registry-driven models.** Builders, rules, planner prompt and costs read `config/models.yaml`; nothing else hardcodes endpoints/fields.
- **Cost controls.** `estimate` before paying; generation refuses above `--budget` and asks above `confirm_above_usd` unless `--yes`; fake generation never records cost.
- **Disclosure + provenance** on every render (`finalize()` refuses empty text; `manifest.json`).

## External services
- **Claude** → `llm/claude.py` only (models/prices in `config.py`; ADR 0003).
- **Higgsfield** → `generation/higgsfield_client.py` only, driven by `generation/scheduler.py` (ADR 0002). Facts and what is still unverified: `docs/higgsfield-notes.md`.
- **ffmpeg/ffprobe** → `media/ffmpeg.py` only.
- **SMTP (email)** → `notify/email.py` only; settings `TOURGEN_SMTP_*` (ADR 0006).
- **Listing sites** → `ingest/listing_url.py` (robots.txt respected; user owns the rights).

## Where to change X (cookbook)
- Add a new video model → entry in `config/models.yaml` (+ `generation/prompt_builders/<family>.py` and a line in `prompt_builders/__init__.py` if it's a new family) + a case in `tests/unit/test_prompt_builders.py` (parametrized over the registry).
- Change tour storytelling → `tourgen/llm/prompts/plan_tour.md` (bump `version:`); hard rules also in `core/rules.py`.
- Change QA strictness → `tourgen/llm/prompts/qa_clip.md` (verdict criteria), `models/qa.py` validator, `stages/qa.py` (`QA_FRAME_COUNT`), retry count via `--max-retries`.
- Change how retries calm a shot → `prompt_builders/base.py::apply_retry_adjustment`.
- Add an input source → new module in `tourgen/ingest/`, call it from `stages/ingest.py`, new `AssetKind` if needed.
- Change render look (cards, labels, transitions, disclosure position) → `media/compose.py`; timings in `config/defaults.yaml`.
- Add voiceover → implement `TTSProvider` in `media/tts.py`, pass it to `stages/assemble.run`; the planner fills `Shot.narration` when the brief asks.
- Change cost/budget defaults → `config.py` (`budget_usd`, `confirm_above_usd`), prices in `models.yaml`.
- Change the email (wording, subject, size budget) → `stages/notify.py` (body/subject), `config.py` (`email_max_attachments_mb`); another delivery channel → new module in `tourgen/notify/` used by `stages/notify.py`.
- Add a preflight check → `core/preflight.py::run_checks`.

## Testing
- `make test` runs everything offline (~1 min): `FakeLLM` + `FakeHiggsfieldClient`, small renders (`render_scale=0.2`).
- Fixture photos are synthetic (`make fixtures` regenerates them); filenames carry room types for `FakeLLM`.
- Claude/Higgsfield clients are tested against stubs / `httpx.MockTransport`.
- QA loop tests inject bad clips (`bad_shots`), scripted verdicts (`qa_script`), failed/NSFW generations.
- `tests/integration/test_url_to_inbox.py` serves a fake listing site on localhost and runs `tourgen run --url … --email …` end to end (outbox + recording SMTP fake).

## Known limitations / open questions
- Higgsfield model endpoints/fields/prices are **unverified** (docs were egress-blocked) — see `docs/higgsfield-notes.md` before the first paid run.
- No recorded real-Claude fixtures yet; offline runs exercise plumbing, not understanding quality.
- 9:16 is a planned crop of landscape clips (upscaled); native vertical generation not implemented.
- Higgsfield webhooks not implemented (polling only); no evaluation set yet (PLAN 7.2).
- HEIC inputs need a Pillow HEIF plugin; they are flagged unreadable otherwise.
- Music library is empty by default (silent bed).
- Email attaches size-reduced videos (≤ 18 MB total by default); no hosted download links yet. SMTP never tested against a real server from the build environment.
- Many listing sites block scrapers or are JS-heavy (install the `browser` extra for the Playwright fallback); the cloud build environment's network policy blocks most of them.
