"""
FILE: tourgen/stages/notify.py
ROLE: Pipeline stage 10. Emails the finished tour to the job's recipients.

HOW IT WORKS:
  1. No recipients → nothing to do (stage completes immediately).
  2. Each final render gets an email-sized copy (media/transcode.py); the byte
     budget (settings.email_max_attachments_mb) is split evenly between renders.
  3. Builds the message (notify/email.py) with title, source listing, disclosure,
     cost and any render that could not be attached, then sends it via SMTP
     (or writes a .eml to render/email/outbox when outbox mode is on).
  4. Writes render/notify.json; its presence means "already sent" so a resumed
     job never emails twice.

READS:  render/manifest.json, render/final_*.mp4, assets/asset_index.json (listing title), job.json
WRITES: render/email/*.mp4, render/email/outbox/*.eml, render/notify.json

CONNECTS TO:
  - Called by core/pipeline.py after stages/deliver.py; docs/decisions/0006-url-to-inbox.md
"""

from __future__ import annotations

from datetime import UTC, datetime

from tourgen.core.context import StageContext, StageResult
from tourgen.media.transcode import fit_to_size
from tourgen.models.assets import AssetIndex
from tourgen.models.delivery import EmailAttachment, Manifest, NotifyRecord
from tourgen.models.job import StageName
from tourgen.notify.email import build_tour_email, sender_for

NAME = StageName.NOTIFY


def is_complete(ctx: StageContext) -> bool:
    return not ctx.job.inputs.notify_email or ctx.workspace.notify_file.exists()


def run(ctx: StageContext) -> StageResult:
    recipients = ctx.job.inputs.notify_email
    if not recipients:
        return StageResult("no recipients; nothing to send")
    ws = ctx.workspace
    manifest = ws.read_model(ws.manifest_file, Manifest)
    budget = int(ctx.settings.email_max_attachments_mb * 1024 * 1024)
    per_file = budget // max(len(manifest.outputs), 1)

    attachments: list[EmailAttachment] = []
    skipped: list[str] = []
    for aspect, rel_path in manifest.outputs.items():
        tag = aspect.replace(":", "x")
        fitted = fit_to_size(ws.abs(rel_path), ws.email_dir / f"tour_{tag}.mp4", per_file)
        if fitted is None:
            skipped.append(aspect)
            continue
        attachments.append(
            EmailAttachment(
                filename=f"{ws.job_id}_{tag}.mp4",
                path=ws.rel(fitted),
                size_bytes=fitted.stat().st_size,
            )
        )

    sender = sender_for(ctx.settings, ws.email_dir / "outbox", ctx.job.options.email_outbox)
    from_address = ctx.settings.smtp_from or ctx.settings.smtp_user or "tourgen@localhost"
    index = ws.read_model_or_none(ws.asset_index_file, AssetIndex)
    # The listing's own title is what the recipient recognises; fall back to the plan's.
    title = (index.listing.title if index and index.listing else None) or manifest.title
    subject = f"Your property tour is ready: {title}"
    msg = build_tour_email(
        from_address,
        recipients,
        subject,
        _body(ctx, manifest, attachments, skipped),
        [(a.filename, ws.abs(a.path)) for a in attachments],
    )
    sender.send(msg)
    record = NotifyRecord(
        recipients=recipients,
        subject=subject,
        sent_at=datetime.now(UTC),
        transport=sender.transport,
        attachments=attachments,
        skipped=skipped,
    )
    ws.write_model(ws.notify_file, record)
    size_mb = sum(a.size_bytes for a in attachments) / 1024 / 1024
    return StageResult(
        f"emailed {len(attachments)} video(s), {size_mb:.1f} MB, to {', '.join(recipients)} "
        f"via {sender.transport}" + (f"; not attached: {', '.join(skipped)}" if skipped else "")
    )


def _body(
    ctx: StageContext,
    manifest: Manifest,
    attachments: list[EmailAttachment],
    skipped: list[str],
) -> str:
    lines = [
        "Hello,",
        "",
        f'Your property tour "{manifest.title}" is ready.',
        "",
    ]
    if ctx.job.inputs.urls:
        lines += ["Made from: " + ", ".join(ctx.job.inputs.urls), ""]
    if attachments:
        lines.append("Attached:")
        lines += [f"  - {a.filename} ({a.size_bytes / 1024 / 1024:.1f} MB)" for a in attachments]
    if skipped:
        lines += [
            "",
            "Too large to attach (full quality copies are in the job folder): "
            + ", ".join(skipped),
        ]
    lines += [
        "",
        f"Shots: {len(manifest.clips)}, "
        f"of which {sum(1 for c in manifest.clips if c.used_fallback)} use a simple pan/zoom "
        "over the original photo instead of AI animation.",
        f"Cost: ${manifest.total_cost_usd:.2f}",
        "",
        f'The video carries this notice: "{manifest.disclosure_text}"',
        "Please keep it when you publish the video.",
        "",
        f"Job id: {manifest.job_id}",
        "— tourgen",
    ]
    return "\n".join(lines)
