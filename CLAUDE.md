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
- Prompt files in tourgen/llm/prompts/ are versioned: bump `version:` in the header on every text change.
- Decisions that deviate from docs/BUILD_PROMPT.md get a short ADR in docs/decisions/.

## Commands
make setup | make lint | make typecheck | make test | make run-sample
tourgen run <input_dir_or_url> --fake-generation
tourgen run --url <listing> --email <address> --yes   # URL → inbox (needs keys + SMTP; check with `tourgen doctor`)
(`--fake-llm` additionally replaces Claude with the offline FakeLLM; needed without ANTHROPIC_API_KEY.)

## Environment notes
- Needs ffmpeg/ffprobe on PATH and a TTF font (default DejaVuSans; override TOURGEN_FONT_PATH).
- Secrets in `.env` (see `.env.example`): ANTHROPIC_API_KEY, HF_KEY (key_id:key_secret), TOURGEN_SMTP_* for email.
- `TOURGEN_RENDER_SCALE=0.3` renders small previews fast.
