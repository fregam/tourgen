# Higgsfield API — verified notes

**Date checked:** 2026-10-08 · **By:** initial build session

## How this was verified (and what was not)

- `docs.higgsfield.ai` (incl. `llms.txt`) and `higgsfield.ai` were **blocked by the build
  environment's egress proxy** (HTTP 403 on CONNECT), so the live docs could not be read.
- Instead, the official Python SDK **`higgsfield-client` 0.2.0** (PyPI, repo
  `github.com/higgsfield-ai/higgsfield-client`) was downloaded and its source read. Everything
  in "Verified from SDK source" below comes from that code, not from this repo's assumptions.
- Model-specific endpoints, field names, durations and prices are **NOT verified**. They are
  marked `verified: false` in `config/models.yaml`. Re-check them against the model pages
  before the first real run (PLAN 0.3 / 3.6).

## Verified from SDK source (higgsfield-client 0.2.0)

| Fact | Detail |
|---|---|
| Base URL | `https://api.higgsfield.ai` |
| Auth header | `Authorization: Key <key_id>:<key_secret>`; SDK reads `HF_KEY`, or `HF_API_KEY` + `HF_API_SECRET` |
| Submit | `POST /<application>` with JSON arguments → `{request_id, status_url, cancel_url}` |
| Status | `GET status_url` → `{"status": ...}`; the SDK also uses `status_url` as the response URL |
| Fallback URLs | `/requests/{id}/status`, `/requests/{id}/cancel` (SDK builds these only when none were returned; we always use returned URLs) |
| Statuses | `queued`, `in_progress`, terminal `completed`, `failed`, `nsfw`, `canceled` |
| Cancel | `POST cancel_url`; only before processing starts (400 otherwise) |
| Webhook | `?hf_webhook=<url>` query parameter on submit |
| Upload | `POST /files/generate-upload-url {"content_type"}` → `{public_url, upload_url, upload_headers}`; `PUT` bytes to `upload_url` with `upload_headers` (fallback `Content-Type` only) using a **separate client without credentials** |
| SDK retries | 408, 429, 500, 502, 503, 504 with exponential backoff (0.2 s × 2ⁿ, 3 tries) |
| Result shape | README example for images: `result['images'][0]['url']`. **Video result shape not shown** → `extract_video_url()` tries `video.url`, `videos[0].url`, `output(s)`, `video_url`, `url`. |

**SDK limitation:** `submit()` cannot send custom headers, so it cannot send an
`Idempotency-Key`. See `docs/decisions/0002-higgsfield-http-client.md`.

## From BUILD_PROMPT, not yet verified

- `Idempotency-Key` header is honoured on submissions.
- Output URLs retained ≥ ~7 days (we download immediately anyway).
- Upload URL expires after 1 hour (we upload right before submitting).
- Concurrency limit returned as HTTP 400 "Maximum number of concurrent requests (N)",
  no `Retry-After` (handled as `ConcurrencyLimitError` + backoff/jitter).
- Model candidates: Kling 3.0 (`image_url`, `last_image_url`, prompt ≤ 2,500 chars, Turbo
  3–15 s without last frame), Seedance 2.0 (`image_url`, `end_image_url`, explicit duration),
  Wan 2.7/3.0 (`image_url`, `end_image_url`). Third-party hosts of the same models (fal.ai,
  RunComfy) list Kling 3.0 durations 3–15 s and Seedance 2.0 durations 4–15 s, but their
  parameter names may differ from Higgsfield's.

## To do before the first paid run (PLAN 3.6)

1. Read `https://docs.higgsfield.ai/docs/llms.txt` and each model page from a network that
   allows it; fix `endpoint`, field names, durations, resolutions and `usd_per_second` in
   `config/models.yaml`; set `verified: true` per entry.
2. Confirm the completed-status JSON for video and simplify `extract_video_url()`.
3. Confirm `Idempotency-Key` support (submit twice with the same key; expect one request).
4. Run one cheap shot: `tourgen new <photos> --job-id hf-smoke --max-shots 1`, then
   `tourgen plan`, `tourgen estimate`, `tourgen generate hf-smoke`. Record the result here.
