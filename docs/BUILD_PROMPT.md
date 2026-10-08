# Build Prompt: AI House Tour Generator (Claude + Higgsfield)

> Paste this whole file into Claude Code at the root of an empty repo, or save it as `docs/BUILD_PROMPT.md` and tell Claude Code: "Read docs/BUILD_PROMPT.md and execute it."

---

## 0. Your mission

Build **`tourgen`**: a tool that takes whatever material exists about a property (photos, videos, a listing URL, an optional floor plan and a short brief) and produces a polished, cinematic house-tour video. Claude does the *understanding, planning, prompting and quality control*. Higgsfield does the *video generation*. ffmpeg does the *assembly*.

The guiding rule of the whole product: **animate the camera, never alter the house.** Every generated clip must stay faithful to the real property. A tour that invents a window, a pool or an extra room is a failed tour, and in many markets a legal liability.

You (Claude Code) are both the builder and the future maintainer of this codebase. Design it so that a fresh agent with zero context can open the repo, read two files, and be productive in under a minute.

---

## 1. First actions (do these before writing feature code)

1. Create `PLAN.md` (structure in §8) and `overview.md` (structure in §9). Create them *first*, as skeletons, then keep them updated as you build.
2. Run `/init` to generate `CLAUDE.md`, then edit it so it contains the content described in §10. The most important line in it: **"Read `overview.md` before exploring the codebase."**
3. Verify the Higgsfield API surface yourself before coding against it (see §4.1). Do not trust model names, parameters or limits written in this prompt over the live docs.
4. Only then start Phase 1 of `PLAN.md`, checking off boxes as you complete them.

---

## 2. Tech stack (decided; change only with a written reason in `docs/decisions/`)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Official Higgsfield Python SDK, strong media/data tooling |
| Packaging | `uv` + `pyproject.toml` | Fast, reproducible |
| Data models | Pydantic v2 | One source of truth for every structure that crosses a boundary (LLM output, API payloads, job state) |
| LLM | Anthropic Python SDK. Default `claude-sonnet-5-5` for per-image analysis and QA; `claude-opus-5-5` for tour planning. Model IDs live in config only. | Vision + reliable structured JSON |
| Video generation | `higgsfield-client` (official Python SDK) | Handles auth, submit, polling, uploads |
| Media | ffmpeg / ffprobe via `subprocess` wrapper, Pillow, `imagehash`, OpenCV (blur/exposure metrics) | Deterministic, scriptable |
| URL ingestion | `httpx` + `selectolax`; Playwright only as fallback for JS-heavy pages | Light by default |
| CLI | Typer | Primary interface; Claude Code can drive it end-to-end |
| API/UI (Phase 6) | FastAPI + a minimal single-page UI | Comes after the pipeline works from the CLI |
| Tests | pytest, with a `FakeHiggsfieldClient` and recorded Claude fixtures | No paid API calls in tests |
| Lint/format/types | ruff, ruff format, mypy (strict on `tourgen/core` and `tourgen/models`) | |

Secrets in `.env` (never committed): `ANTHROPIC_API_KEY`, `HF_KEY` (format `key_id:key_secret`). Provide `.env.example`.

---

## 3. The pipeline (how it works end-to-end)

Each stage reads typed inputs from the job workspace and writes typed outputs back. Every stage is **idempotent and resumable**: re-running a job skips stages whose outputs already exist and are valid.

```
INPUTS ─► 1 Ingest ─► 2 Normalize ─► 3 Understand ─► 4 Plan ─► 5 Compile prompts
        ─► 6 Generate ─► 7 QA (loop back to 5/6 on failure) ─► 8 Assemble ─► 9 Deliver
```

1. **Ingest** — Collect raw assets.
   - Local photos/videos from a folder or file list.
   - Videos: probe with ffprobe; extract keyframes via ffmpeg scene detection plus a few evenly spaced frames; keep original clips too (they may be usable B-roll as-is).
   - Listing URL: fetch page, extract image URLs (prefer largest srcset variant), title, description, room count, surface area. Respect robots.txt; the user is responsible for having rights to the material, so print a one-line reminder.
   - Floor plan (optional): stored as a special asset type.
   - Brief (optional): free text such as target audience, tone, language, length, aspect ratios, agent branding.
2. **Normalize** — Fix EXIF orientation, convert to JPEG/PNG, cap resolution, drop near-duplicates (perceptual hash), compute quality metrics (resolution, blur via Laplacian variance, exposure histogram). Low-quality images are flagged, not silently deleted.
3. **Understand (Claude vision)** — One call per image (batched where useful) returning a strict `ImageAnalysis`: room type, indoor/outdoor, notable features, framing (wide/medium/detail), visible openings (doors, windows, passages) and where they lead if inferable, lighting, defects, and a **motion suitability score** with the camera moves that are safe for this specific framing. Then one aggregate call builds a `PropertyModel`: rooms, which images belong to which room, a best-guess adjacency graph (use floor plan if present), hero shots.
4. **Plan (Claude)** — Produce a `TourPlan`: an ordered sequence of `Shot`s that tells a story (typical arc: exterior approach → entry → living spaces → kitchen → bedrooms → bathrooms → outdoor/view → closing hero shot). For each shot: source image(s), shot type (single-image move, or first→last frame transition between two real photos of adjacent spaces), camera move, duration, intended model, narrative purpose. Also: narration script per shot (optional), on-screen text, music mood, total target length. Hard rules the planner must follow are in §5.
5. **Compile prompts** — Turn each `Shot` into a model-specific request payload via a **prompt builder per model** that enforces that model's constraints (prompt length, allowed durations, which frame fields exist). Prompt style: one clear camera instruction + one light/atmosphere cue + explicit preservation clauses ("keep architecture, furniture and layout exactly as in the image; no new objects; no people"). Output is a validated payload, not free text.
6. **Generate (Higgsfield)** — Upload local images through presigned upload URLs, submit each payload with an `Idempotency-Key` derived from (job_id, shot_id, attempt), persist the `request_id` immediately, cap concurrency with a semaphore sized from config, poll with backoff + jitter (webhook support later), and **download the finished MP4 to local storage at once** because Higgsfield output URLs are only guaranteed for a limited time.
7. **QA (Claude vision)** — Extract frames from each generated clip (first, middle, last + a few more), send them alongside the source photo(s) to Claude and ask for a structured `QAReport`: fidelity score, list of hallucinations (added/removed/warped elements), geometry warping, flicker, artifacts, verdict `pass | retry | reject`. On `retry`, feed the specific failure back into prompt compilation (e.g. reduce motion strength, shorten duration, switch model, simplify camera move) up to `max_retries`. On `reject`, drop the shot and let the planner re-sequence or fall back to a Ken Burns move generated locally by ffmpeg (zero hallucination risk).
8. **Assemble (ffmpeg)** — Normalize all clips (fps, resolution, color range), apply transitions, add music bed, optional voiceover (pluggable TTS provider, off by default), title card, room labels, agent branding, and a **mandatory disclosure line** such as "Video animated with AI from real property photos." Render each requested aspect ratio (16:9 and 9:16 at minimum; 9:16 uses planned reframing, not blind cropping).
9. **Deliver** — Final videos + `manifest.json` (provenance: every output clip ↔ source asset ↔ prompt ↔ model ↔ request_id ↔ QA verdict) + a cost/time report.

### Job workspace layout (state lives on disk, not in memory)

```
jobs/<job_id>/
  job.json              # JobState: stage statuses, config snapshot, costs, errors
  inputs/               # raw ingested files
  assets/               # normalized images + asset_index.json
  analysis/             # image_analyses.json, property_model.json
  plan/                 # tour_plan.json, payloads/<shot_id>.json
  clips/                # <shot_id>__attempt<N>.mp4 + request records
  qa/                   # frames/, <shot_id>__attempt<N>.qa.json
  render/               # final_16x9.mp4, final_9x16.mp4, manifest.json, report.md
```

---

## 4. External services: what to know

### 4.1 Higgsfield API (verify against live docs before coding)

Starting point for you: **https://docs.higgsfield.ai/docs/llms.txt**. Read it, then each model page you intend to use (each model page has its own endpoint and parameters). Write what you verified into `docs/higgsfield-notes.md` with the date checked.

Known at time of writing (re-verify):
- Base URL `https://api.higgsfield.ai`; auth header `Authorization: Key <key_id>:<key_secret>`. SDK reads `HF_KEY`.
- Generation is **asynchronous**: submit → `request_id` + `status_url` + `cancel_url`. Statuses: `queued`, `in_progress`, terminal `completed`, `failed`, `nsfw`, `canceled`. Use the URLs returned, never construct them.
- Send an `Idempotency-Key` header on submissions so ambiguous timeouts can be retried safely.
- Output URLs are retained for at least ~7 days → copy to own storage immediately.
- File inputs must be public HTTPS URLs. For local files: `POST /files/generate-upload-url` with `content_type` → PUT the file to `upload_url` with every header in `upload_headers` (never send Higgsfield credentials there) → use the returned `public_url`. Upload URL expires after 1 hour.
- Rate limiting is mainly **concurrency** (e.g. "Maximum number of concurrent requests (4)" returned as HTTP 400), no `Retry-After` header → semaphore + backoff/jitter; read the account's real limit from config.
- Candidate image-to-video models (each with its own endpoint and constraints):
  - **Kling 3.0** (`kling-video/v3.0/...`): `image_url` = first frame, optional `last_image_url`; multi-shot mode; prompt ≤ 2,500 chars; Turbo variant durations 3–15 s, 720p/1080p, no last-frame field.
  - **Seedance 2.0** (`bytedance/seedance-2.0/image-to-video`): required `image_url`, optional `end_image_url`; explicit duration required.
  - **Wan 2.7 / 3.0** (`wan/...`): `image_url`, optional `end_image_url`.
- Build a **model registry** (`config/models.yaml`) describing each model: endpoint, frame fields, duration range, resolutions, prompt limit, supports_last_frame, relative cost, notes. Prompt builders and the planner read capabilities from the registry, so adding a model = adding a YAML entry + (maybe) a builder.

### 4.2 Claude API

- All Claude calls go through one module (`tourgen/llm/claude.py`) that handles retries, image encoding (base64, resize to a sane max dimension first), token/cost accounting, and **structured output parsing into Pydantic models** (validate; on validation failure, re-ask once with the validation error).
- Prompts live as versioned text files in `tourgen/llm/prompts/` (not inline strings) so they can be iterated and diffed. Each prompt file starts with a comment block: purpose, inputs, output schema name, version.
- Record every request/response in the job workspace (`analysis/_llm_log/`) for debugging and for building test fixtures.

### 4.3 ffmpeg

Single wrapper module that builds commands as lists, logs the exact command, captures stderr, and raises typed errors. No shell string interpolation.

---

## 5. Creative + fidelity rules (enforced in planner prompts AND in code validation)

- One camera move per shot. Slow. Motivated. Favor: slow dolly-in, lateral slide/track, gentle pan, subtle orbit on islands/feature objects, crane/rise on exteriors. Avoid: fast moves, spins, moves that require revealing large unseen areas.
- Wide photos get movement; detail photos get minimal movement or are used as short inserts.
- First→last frame transitions only between two real photos of spaces the planner believes are adjacent, and only when both are compatible (similar light, plausible path). Otherwise use a cut or dissolve.
- Shot durations typically 3–6 s; total tour length from the brief (default 45–75 s for social, 90–150 s for full tour).
- Never ask a model to add, remove, restyle or "improve" anything. Virtual staging is out of scope for v1 (if ever added, it must be a separate, clearly labelled mode).
- Exterior first, strongest hero shot last (or first, per brief).
- Every final render carries the AI disclosure line, plus the manifest for provenance.

`tourgen/core/rules.py` must encode the checkable rules (durations within model range, no shot without a source image, transition pairs adjacent in `PropertyModel`, disclosure present) and run them on the `TourPlan` before any paid generation.

---

## 6. Cost and safety controls

- `tourgen estimate <job>` prints expected Higgsfield + Claude cost and time **before** generation. Generation requires `--yes` or an interactive confirm above a configurable budget.
- `--dry-run` runs everything through Plan + Compile and writes payloads without calling Higgsfield.
- `--max-shots`, `--max-retries`, `--budget` flags.
- `FakeHiggsfieldClient` (returns a local placeholder MP4 after a simulated delay, can simulate `failed`, `nsfw`, concurrency errors) is the default in tests and selectable via `--fake-generation` for development.

---

## 7. Code structure (target; adjust if you have a good reason, then update overview.md)

```
tourgen/
  cli.py                     # Typer entrypoint: new, ingest, analyze, plan, estimate, generate, qa, render, run, status
  config.py                  # Settings (pydantic-settings): env vars, paths, model IDs, concurrency, budgets
  models/                    # ALL Pydantic schemas. No logic beyond validators.
    assets.py                #   Asset, AssetQuality, AssetIndex
    analysis.py              #   ImageAnalysis, PropertyModel, Room, Adjacency
    plan.py                  #   TourPlan, Shot, CameraMove, Transition, NarrationLine
    generation.py            #   GenerationPayload, GenerationRecord, RequestStatus
    qa.py                    #   QAReport, Hallucination, Verdict
    job.py                   #   JobState, StageStatus, CostLedger
  core/
    pipeline.py              # Orchestrator: runs stages in order, handles resume/skip, updates job.json
    workspace.py             # All filesystem paths for a job; the ONLY place that knows the layout in §3
    rules.py                 # Deterministic validation of TourPlan against §5 rules + model registry
    costs.py                 # Cost estimation and ledger
  stages/                    # One file per pipeline stage. Each exposes run(ctx) -> StageResult.
    ingest.py
    normalize.py
    understand.py
    plan.py
    compile_prompts.py
    generate.py
    qa.py
    assemble.py
    deliver.py
  ingest/
    local.py                 # folders/files
    video_frames.py          # keyframe extraction from videos
    listing_url.py           # HTML scraping (+ playwright fallback)
  llm/
    claude.py                # Single Claude client wrapper (retries, images, structured output, cost)
    prompts/                 # Versioned prompt files: analyze_image.md, build_property_model.md, plan_tour.md, qa_clip.md
  generation/
    registry.py              # Loads config/models.yaml into typed ModelSpec objects
    higgsfield_client.py     # Real client: upload, submit (idempotent), poll, download
    fake_client.py           # Test/dev double with same interface
    prompt_builders/         # One builder per model family: kling.py, seedance.py, wan.py, base.py
    scheduler.py             # Semaphore-bounded submission + polling loop, retry policy
  media/
    ffmpeg.py                # Safe command wrapper
    quality.py               # blur, exposure, resolution, perceptual hash
    frames.py                # extract frames from clips for QA
    compose.py               # transitions, overlays, audio, aspect-ratio renders
    kenburns.py              # local fallback motion (zero-hallucination)
  api/                       # Phase 6: FastAPI app + static UI
config/
  models.yaml                # Higgsfield model registry
  defaults.yaml              # Default tour settings (lengths, transitions, music mood, disclosure text)
tests/
  fixtures/                  # Sample property photos, recorded Claude responses, tiny MP4s
  unit/  integration/
docs/
  higgsfield-notes.md        # Verified API facts + date checked
  decisions/                 # Short ADRs: 0001-python-stack.md, etc.
  BUILD_PROMPT.md            # This file
PLAN.md  overview.md  CLAUDE.md  README.md  .env.example  pyproject.toml  Makefile
```

Design rules for an AI-maintained codebase:
- **Small files, single responsibility.** Aim < 300 lines per file. If a file grows past that, split it and update `overview.md`.
- **Schemas are the contract.** Stages communicate only through `tourgen/models/*` objects serialized into the workspace. Never pass loose dicts between stages.
- **One place per concern.** Paths → `workspace.py`. Claude calls → `llm/claude.py`. Higgsfield calls → `generation/higgsfield_client.py`. ffmpeg → `media/ffmpeg.py`. A grep for the concern should hit one file.
- **Explicit names over clever ones.** `compile_kling_payload(shot, spec)` not `build(s, m)`.
- **Every external call is behind an interface with a fake.**
- `Makefile` targets: `setup`, `lint`, `typecheck`, `test`, `run-sample` (full pipeline on fixtures with fake generation). Agents should run `make lint typecheck test` before checking off a step.

---

## 8. `PLAN.md` — required structure

Use `[ ]` checkboxes and check them `[x]` as you finish. Never delete completed items; add new sub-steps as you discover them. Each phase ends with a **Definition of done** checkbox that is verified by running something.

```markdown
# PLAN — tourgen
Status legend: [ ] todo · [x] done · [~] in progress · [!] blocked (add reason)
Last updated: <date> by <agent session note>

## Phase 0 — Foundations
- [ ] 0.1 Repo scaffold: pyproject (uv), ruff, mypy, pytest, Makefile, .env.example, .gitignore
- [ ] 0.2 Create overview.md skeleton and CLAUDE.md via /init, edit per BUILD_PROMPT §10
- [ ] 0.3 Verify Higgsfield docs (llms.txt + chosen model pages); write docs/higgsfield-notes.md
- [ ] 0.4 config.py settings + config/models.yaml registry + registry.py loader with tests
- [ ] 0.5 All Pydantic schemas in tourgen/models/ with round-trip tests
- [ ] 0.6 workspace.py + JobState + pipeline.py skeleton with resume/skip logic
- [ ] DoD: `make lint typecheck test` green; `tourgen new` creates a job workspace

## Phase 1 — Ingest & Normalize
- [ ] 1.1 Local folder ingest
- [ ] 1.2 Video keyframe extraction
- [ ] 1.3 Listing URL ingest (+ robots/rights reminder)
- [ ] 1.4 Normalize: EXIF, format, resize, dedupe (phash), quality metrics
- [ ] DoD: sample fixtures → assets/asset_index.json with quality flags

## Phase 2 — Understand & Plan (Claude)
- [ ] 2.1 llm/claude.py wrapper (images, structured output, retry-on-validation-error, cost)
- [ ] 2.2 analyze_image prompt + stage → ImageAnalysis per asset
- [ ] 2.3 build_property_model prompt → PropertyModel (rooms, adjacency, hero shots)
- [ ] 2.4 plan_tour prompt → TourPlan
- [ ] 2.5 rules.py validation; planner re-asked with violations if invalid
- [ ] 2.6 Recorded-response fixtures so tests run offline
- [ ] DoD: `tourgen plan <job>` produces a valid, rule-passing tour_plan.json on fixtures

## Phase 3 — Compile & Generate (Higgsfield)
- [ ] 3.1 Prompt builders per model family, constrained by registry
- [ ] 3.2 fake_client.py
- [ ] 3.3 higgsfield_client.py: presigned upload, idempotent submit, poll, download
- [ ] 3.4 scheduler.py: semaphore, backoff+jitter, concurrency-error handling, persistence of request_ids
- [ ] 3.5 `estimate`, `--dry-run`, `--budget` controls
- [ ] 3.6 One real end-to-end shot against Higgsfield (manual, cheap model) — record result in higgsfield-notes.md
- [ ] DoD: fake run completes all shots; one real clip downloaded to clips/

## Phase 4 — QA loop
- [ ] 4.1 Frame extraction for QA
- [ ] 4.2 qa_clip prompt → QAReport (source vs generated)
- [ ] 4.3 Retry policy: adjust payload from QA failure reasons; max_retries
- [ ] 4.4 Ken Burns fallback for rejected shots
- [ ] DoD: injected bad clip is caught and retried/fallen back

## Phase 5 — Assemble & Deliver
- [ ] 5.1 Clip normalization (fps, resolution, color)
- [ ] 5.2 Transitions, titles, room labels, branding
- [ ] 5.3 Music bed; optional TTS voiceover behind interface (off by default)
- [ ] 5.4 Mandatory AI disclosure overlay
- [ ] 5.5 16:9 and 9:16 renders
- [ ] 5.6 manifest.json (provenance) + report.md (costs, timings, QA summary)
- [ ] DoD: `tourgen run <inputs>` produces final videos end-to-end with fake generation

## Phase 6 — Interface
- [ ] 6.1 FastAPI: create job, upload, start, status (SSE), download
- [ ] 6.2 Minimal UI: upload → review/edit TourPlan (reorder, drop shots) → approve cost → watch progress → download
- [ ] 6.3 Webhook receiver for Higgsfield completions (polling stays as recovery)
- [ ] DoD: full flow from browser

## Phase 7 — Hardening
- [ ] 7.1 Structured logging + per-job log file
- [ ] 7.2 Prompt/version evaluation set (5+ sample properties, scored QA pass rate)
- [ ] 7.3 README for humans
```

---

## 9. `overview.md` — required structure

This is the map a new agent reads first. Keep it accurate; **any PR that adds, moves, renames or changes the responsibility of a file must update it.** Keep it under ~400 lines; link out to docs for depth.

```markdown
# overview — tourgen
## What this is (3–5 lines)
## Run it (the 5 commands that matter)
## Pipeline at a glance (diagram from BUILD_PROMPT §3 + one line per stage)
## Data flow (which schema each stage reads and writes, and where on disk)
| Stage | Reads | Writes | File |
## Code map (every file: path — one-line responsibility — key collaborators)
## Key concepts & invariants (fidelity rules, idempotency, workspace-as-state, registry-driven models)
## External services (Claude, Higgsfield, ffmpeg: which module owns each; link docs/higgsfield-notes.md)
## Where to change X (cookbook)
- Add a new video model → config/models.yaml + generation/prompt_builders/<x>.py + test
- Change tour storytelling → tourgen/llm/prompts/plan_tour.md (bump version)
- Change QA strictness → ...
- Add an input source → ...
## Testing (fakes, fixtures, how to run offline)
## Known limitations / open questions
```

---

## 10. `CLAUDE.md` (generate with `/init`, then make sure it contains this)

```markdown
# CLAUDE.md
## Start here
1. Read `overview.md` first — it is the map of the codebase (pipeline, data flow, code map, where-to-change-X).
2. Read `PLAN.md` to see what's done and what's next. Work on the first unchecked item unless told otherwise.
3. Only then open source files. Every file starts with a header comment explaining its role and connections.

## Working rules
- After finishing a step: run `make lint typecheck test`, check the box in PLAN.md, update overview.md if structure or responsibilities changed.
- Never call real Higgsfield in tests. Use FakeHiggsfieldClient. Real calls only when explicitly asked, and run `tourgen estimate` first.
- Never hardcode Higgsfield endpoints/params outside config/models.yaml and generation/. Verify against docs.higgsfield.ai (llms.txt) when unsure; record findings in docs/higgsfield-notes.md.
- Schemas in tourgen/models/ are the contract between stages. Change them deliberately and update dependents + overview.md.
- Fidelity rule: animate the camera, never alter the property. Don't write prompts that add/remove/restyle content.
- Keep files small and single-purpose. New file → header comment + overview.md entry.

## Commands
make setup | make lint | make typecheck | make test | make run-sample
tourgen run <input_dir_or_url> --fake-generation
```

---

## 11. File header comments (mandatory in every source file)

Every `.py` file (and every prompt file in `llm/prompts/`) starts with this block. Keep it truthful; update it when the file changes.

```python
"""
FILE: tourgen/stages/qa.py
ROLE: Pipeline stage 7. Checks each generated clip against its source photo(s)
      and decides pass / retry / reject.

HOW IT WORKS:
  1. Extracts N frames from the clip (media/frames.py).
  2. Sends frames + source images to Claude with prompts/qa_clip.md (llm/claude.py).
  3. Parses a QAReport; on "retry", writes adjustment hints that
     stages/compile_prompts.py reads on the next attempt.

READS:  clips/<shot_id>__attempt<N>.mp4, plan/tour_plan.json, assets/
WRITES: qa/<shot_id>__attempt<N>.qa.json, qa/frames/

CONNECTS TO:
  - Called by core/pipeline.py
  - Uses media/frames.py, llm/claude.py, models/qa.py
  - Feeds back into stages/compile_prompts.py and stages/generate.py

INVARIANTS / GOTCHAS:
  - A clip with any "structural" hallucination (new/removed window, door, wall) is never "pass".
  - Never deletes clips; failed attempts stay on disk for debugging.
"""
```

Inside files: comment the *why*, not the *what*. Non-obvious decisions get a one-line comment pointing to the ADR or doc that justifies them.

---

## 12. Quality bar for "best version"

The tool is good when, given ~15–25 ordinary listing photos, it produces in one command a 60–90 s tour that: flows in a believable spatial order, has smooth slow camera moves, contains zero structural hallucinations in the final cut, includes the disclosure, comes in 16:9 and 9:16, costs within the estimate, and can be fully re-run or resumed without duplicate charges. Build an evaluation set early (Phase 7.2 can start earlier) so prompt changes are measured, not guessed.

## 13. When unsure

Make a reasonable decision, write it in `docs/decisions/NNNN-title.md` (context, decision, consequences, 10 lines max), note it in `PLAN.md`, and keep going. Ask the human only for: API keys, budget limits, rights to input material, or choices that change cost by more than ~2×.