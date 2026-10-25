"""
FILE: tourgen/cli_options.py
ROLE: Shared Typer option declarations and the flags → (JobInputs, JobOptions) translation
      used by `new`, `run` and `doctor` in cli.py.

HOW IT WORKS:
  - Option objects are module constants so every command spells a flag the same way.
  - build_job_spec() validates emails, builds the Brief and fills defaults from Settings.

CONNECTS TO:
  - cli.py; models/job.py; notify/email.py (address validation)

INVARIANTS / GOTCHAS:
  - No pipeline logic here; just flag translation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import typer

from tourgen.config import Settings
from tourgen.models.job import Brief, JobInputs, JobOptions
from tourgen.notify.email import valid_email

INPUTS = typer.Argument(None, help="Photo/video files or folders")
JOB_ID = typer.Option(None, "--job-id", help="Defaults to a timestamp")
URL = typer.Option([], "--url", help="Listing URL to scrape (repeatable)")
EMAIL = typer.Option([], "--email", help="Email the finished videos to this address (repeatable)")
EMAIL_OUTBOX = typer.Option(
    False, "--email-outbox", help="Write the email to render/email/outbox instead of sending"
)
FLOOR_PLAN = typer.Option(None, "--floor-plan")
BRIEF = typer.Option("", "--brief", help="Free-text brief (audience, tone, agent…)")
BRIEF_FILE = typer.Option(None, "--brief-file", help="JSON matching models.job.Brief")
FORMAT = typer.Option("social", "--format", help="social (45–75s) | full (90–150s)")
LENGTH = typer.Option(None, "--length", help="Target length in seconds")
ASPECT = typer.Option([], "--aspect", help="Repeatable, e.g. --aspect 16:9")
FAKE_GENERATION = typer.Option(False, "--fake-generation", help="No Higgsfield calls")
FAKE_LLM = typer.Option(False, "--fake-llm", help="Offline filename-based fake Claude")
DRY_RUN = typer.Option(False, "--dry-run", help="Stop after compiling payloads")
MAX_SHOTS = typer.Option(None, "--max-shots")
MAX_RETRIES = typer.Option(None, "--max-retries")
BUDGET = typer.Option(None, "--budget", help="Hard cap in USD")


@dataclass
class JobSpec:
    inputs: JobInputs
    options: JobOptions


def build_job_spec(
    settings: Settings,
    inputs: list[Path] | None,
    url: list[str],
    email: list[str],
    email_outbox: bool,
    floor_plan: Path | None,
    brief: str,
    brief_file: Path | None,
    fmt: str,
    length: float | None,
    aspect: list[str],
    fake_generation: bool,
    fake_llm: bool,
    dry_run: bool,
    max_shots: int | None,
    max_retries: int | None,
    budget: float | None,
) -> JobSpec:
    bad = [address for address in email if not valid_email(address)]
    if bad:
        raise typer.BadParameter(f"invalid email address: {', '.join(bad)}", param_hint="--email")
    data: dict[str, object] = json.loads(brief_file.read_text()) if brief_file else {}
    job_brief = Brief.model_validate(data)
    if brief:
        job_brief.text = brief
    job_brief.format = fmt or job_brief.format
    if length:
        job_brief.target_length_s = length
    if aspect:
        job_brief.aspect_ratios = aspect
    job_inputs = JobInputs(
        paths=[str(p.resolve()) for p in (inputs or [])],
        urls=url,
        floor_plan=str(floor_plan.resolve()) if floor_plan else None,
        brief=job_brief,
        notify_email=email,
    )
    options = JobOptions(
        fake_generation=fake_generation,
        fake_llm=fake_llm,
        dry_run=dry_run,
        email_outbox=email_outbox,
        max_shots=max_shots or settings.max_shots,
        max_retries=settings.max_retries if max_retries is None else max_retries,
        budget_usd=budget or settings.budget_usd,
    )
    return JobSpec(job_inputs, options)
