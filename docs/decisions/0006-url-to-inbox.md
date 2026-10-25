# 0006 — URL in, email out (stage 10 NOTIFY + preflight)

**Context:** The owner wants one action: give a listing URL, receive the finished tour by
email. BUILD_PROMPT §3 ends at "Deliver" (files on disk).
**Decision:** Add stage 10 `notify` after `deliver`: email-sized copies of each render
(`media/transcode.py`, total budget `TOURGEN_EMAIL_MAX_ATTACHMENTS_MB`, default 18 MB to fit
~25 MB provider caps after base64) sent over plain SMTP (`notify/email.py`; Gmail works with
an app password). `render/notify.json` marks it sent so resumes never email twice; `--email-outbox`
writes a `.eml` instead. `tourgen run` first runs `core/preflight.py` checks (keys, tools,
network to Claude/Higgsfield/listing/SMTP) and refuses to start a job that would fail midway.
**Consequences:** Large full-length tours may be attached at reduced bitrate or skipped (noted
in the email; masters stay in the job folder). No hosted download links yet; adding cloud
storage links would be a new notifier behind the same stage.
