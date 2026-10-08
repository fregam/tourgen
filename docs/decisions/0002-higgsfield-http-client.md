# 0002 — Talk to Higgsfield with httpx, mirroring the SDK

**Context:** `higgsfield-client` 0.2.0 `submit()` cannot send custom headers, but resumable,
duplicate-free generation needs an `Idempotency-Key` on every submission.
**Decision:** `generation/higgsfield_client.py` calls the same endpoints the SDK uses
(`docs/higgsfield-notes.md`) via httpx, adds `Idempotency-Key`, and classifies errors
(concurrency 400 → retryable). The SDK is not a dependency.
**Consequences:** We own wire-protocol drift; re-verify against the SDK/docs when upgrading.
If the SDK gains header support, switching back is local to this one file.
