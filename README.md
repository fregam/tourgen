# tourgen — AI house tour generator

Turn listing photos (plus optional videos, a listing URL, a floor plan and a short brief) into
a cinematic property tour in 16:9 and 9:16.

- **Claude** analyses every photo, maps rooms and how they connect, plans the tour, writes the
  video prompts and checks every generated clip against the real photos.
- **Higgsfield** image-to-video models animate the camera.
- **ffmpeg** assembles the cut: titles, room labels, transitions, music and an AI disclosure line.

The rule behind every decision: **animate the camera, never alter the house.** A clip that
invents or removes a window, door or room fails QA and is regenerated more calmly or replaced
by a simple pan/zoom over the real photo. Each video ships with a manifest that links every
clip to its source photo, prompt, model and QA verdict.

## Requirements
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- ffmpeg and ffprobe on your PATH
- For real runs: an Anthropic API key and a Higgsfield API key (`.env`, see `.env.example`)

## Quick start
```bash
make setup
make run-sample          # offline demo: fake Claude + fake Higgsfield on sample photos
open jobs/sample/render/final_16x9.mp4
```

Real run, step by step (nothing is paid for until `generate`):
```bash
cp .env.example .env     # fill in ANTHROPIC_API_KEY and HF_KEY
tourgen new ./my-photos --job-id house1 --brief "young families, warm tone" --format social
tourgen ingest house1    # copy, orient, dedupe, quality-check
tourgen analyze house1   # Claude looks at every photo
tourgen plan house1      # tour plan + model payloads (see jobs/house1/plan/)
tourgen estimate house1  # expected cost and time
tourgen generate house1  # asks for confirmation above the configured threshold
tourgen qa house1        # checks clips, retries or falls back
tourgen render house1    # final videos, manifest.json, report.md
```
Or all at once: `tourgen run ./my-photos --job-id house1 --yes --budget 20`.
Any command can be re-run; finished work is reused, never paid for twice.

## From a listing link to your inbox
```bash
tourgen doctor --url https://listing.example/123 --email you@example.com   # what's missing?
tourgen run --url https://listing.example/123 --email you@example.com --yes --budget 20
```
This scrapes the listing's photos, builds the tour and emails both videos (16:9 and 9:16,
compressed to fit email limits; full quality stays in `jobs/<id>/render/`). Email needs SMTP
settings in `.env` (see `.env.example`; for Gmail use an app password). Add `--email-outbox`
to write the email as a `.eml` file instead of sending it. `tourgen send <job> --to …` re-sends.
The web UI (`tourgen serve`) has the same fields: listing URL and email.

Web UI: `tourgen serve`, then open http://127.0.0.1:8000.

## Useful flags
`--dry-run` (stop before Higgsfield) · `--budget USD` · `--max-shots N` · `--max-retries N` ·
`--url <listing>` · `--email you@x.com` · `--email-outbox` · `--floor-plan plan.png` · `--aspect 9:16` · `--fake-generation` · `--fake-llm`

## Before your first paid run
Higgsfield model endpoints and prices in `config/models.yaml` have not been verified against
the live docs yet. Read `docs/higgsfield-notes.md` first.

## Rights and disclosure
Only use photos and videos you have the rights to. Every render carries the line
"Video animated with AI from real property photos." (configurable in `config/defaults.yaml`,
but never removable).

## For developers and agents
Start with `CLAUDE.md`, then `overview.md` (code map) and `PLAN.md` (what's next).
