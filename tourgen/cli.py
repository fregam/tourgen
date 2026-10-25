"""
FILE: tourgen/cli.py
ROLE: Typer entrypoint — the primary interface. Commands: new, ingest, analyze, plan,
      estimate, generate, qa, render, send, run, doctor, status, serve.

HOW IT WORKS:
  - `new` creates a job workspace (core/pipeline.create_job); the stage commands
    run the pipeline up to a given stage (resumable, already-done stages skip).
  - `run` = preflight checks + new (if needed) + every stage, ending with the email
    when --email is given (`tourgen run --url <listing> --email me@x.com --yes`).
    Budget confirmation: generation stops with ConfirmationRequired unless --yes
    or the user confirms interactively.
  - `doctor` prints the preflight checks (core/preflight.py) without running anything.
  - Shared flags and flag → JobInputs/JobOptions translation live in cli_options.py;
    logging, progress output and the cost-confirmation loop live in cli_runner.py.

CONNECTS TO:
  - core/pipeline.py, core/costs.py, core/preflight.py, cli_options.py, config.py,
    api/app.py (serve)

INVARIANTS / GOTCHAS:
  - Never put pipeline logic here; commands only translate flags into JobOptions
    and call core/pipeline.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from tourgen import cli_options as opt
from tourgen.cli_runner import execute, setup_logging
from tourgen.config import Settings
from tourgen.core.costs import estimate_job
from tourgen.core.pipeline import create_job, open_context
from tourgen.core.preflight import blocking_failures, run_checks
from tourgen.ingest.listing_url import RIGHTS_REMINDER
from tourgen.models.job import StageName, StageStatus
from tourgen.models.plan import TourPlan

app = typer.Typer(
    help="AI house tour generator (Claude + Higgsfield + ffmpeg).", no_args_is_help=True
)


def _settings() -> Settings:
    return Settings()


def _create(spec: opt.JobSpec, job_id: str | None) -> str:
    if spec.inputs.urls:
        typer.echo(RIGHTS_REMINDER)
    job_id = job_id or datetime.now(UTC).strftime("job-%Y%m%d-%H%M%S")
    workspace = create_job(_settings(), job_id, spec.inputs, spec.options)
    typer.echo(f"Created job {job_id} at {workspace.root}")
    return job_id


@app.command()
def new(
    inputs: list[Path] = opt.INPUTS,
    job_id: str = opt.JOB_ID,
    url: list[str] = opt.URL,
    email: list[str] = opt.EMAIL,
    email_outbox: bool = opt.EMAIL_OUTBOX,
    floor_plan: Path | None = opt.FLOOR_PLAN,
    brief: str = opt.BRIEF,
    brief_file: Path | None = opt.BRIEF_FILE,
    fmt: str = opt.FORMAT,
    length: float | None = opt.LENGTH,
    aspect: list[str] = opt.ASPECT,
    fake_generation: bool = opt.FAKE_GENERATION,
    fake_llm: bool = opt.FAKE_LLM,
    dry_run: bool = opt.DRY_RUN,
    max_shots: int = opt.MAX_SHOTS,
    max_retries: int = opt.MAX_RETRIES,
    budget: float = opt.BUDGET,
) -> None:
    """Create a job workspace."""
    spec = opt.build_job_spec(
        _settings(), inputs, url, email, email_outbox, floor_plan, brief, brief_file, fmt,
        length, aspect, fake_generation, fake_llm, dry_run, max_shots, max_retries, budget,
    )  # fmt: skip
    _create(spec, job_id)


def _stage_command(job_id: str, until: StageName, yes: bool, force: bool, verbose: bool) -> None:
    setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    execute(ctx, until, yes, {until} if force else set())


@app.command()
def ingest(job_id: str, force: bool = False, verbose: bool = False) -> None:
    """Stages 1–2: ingest and normalize inputs."""
    _stage_command(job_id, StageName.NORMALIZE, False, force, verbose)


@app.command()
def analyze(job_id: str, force: bool = False, verbose: bool = False) -> None:
    """Stage 3: Claude image analysis + property model."""
    _stage_command(job_id, StageName.UNDERSTAND, False, force, verbose)


@app.command()
def plan(job_id: str, force: bool = False, verbose: bool = False) -> None:
    """Stages 4–5: tour plan (rule-validated) + compiled payloads."""
    _stage_command(job_id, StageName.COMPILE, False, force, verbose)


@app.command()
def estimate(job_id: str) -> None:
    """Print expected Higgsfield + Claude cost and time before generation."""
    ctx = open_context(_settings(), job_id)
    tour = ctx.workspace.read_model_or_none(ctx.workspace.tour_plan_file, TourPlan)
    if tour is None:
        typer.echo("No plan yet — run `tourgen plan` first.", err=True)
        raise typer.Exit(1)
    result = estimate_job(tour, ctx.registry, ctx.settings, ctx.job.options.max_retries)
    for line in result.lines():
        typer.echo(line)
    typer.echo(
        f"Budget: ${ctx.job.options.budget_usd:.2f}; spent so far ${ctx.job.ledger.total():.2f}"
    )


@app.command()
def generate(
    job_id: str,
    yes: bool = typer.Option(False, "--yes", help="Skip the cost confirmation"),
    budget: float = typer.Option(None, "--budget"),
    verbose: bool = False,
) -> None:
    """Stage 6: generate clips (asks for confirmation above the configured threshold)."""
    setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    if budget:
        ctx.job.options.budget_usd = budget
    ctx.job.options.dry_run = False
    execute(ctx, StageName.GENERATE, yes, set())


@app.command()
def qa(job_id: str, yes: bool = False, verbose: bool = False) -> None:
    """Stage 7: QA every clip, retry or fall back."""
    _stage_command(job_id, StageName.QA, yes, False, verbose)


@app.command()
def render(job_id: str, yes: bool = False, force: bool = False, verbose: bool = False) -> None:
    """Stages 8–9: assemble final videos, manifest and report."""
    setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    execute(ctx, StageName.DELIVER, yes, {StageName.ASSEMBLE} if force else set())


@app.command()
def send(
    job_id: str,
    to: list[str] = typer.Option([], "--to", help="Recipients (default: the job's --email)"),
    outbox: bool = typer.Option(False, "--outbox", help="Write .eml instead of sending"),
    verbose: bool = False,
) -> None:
    """Stage 10: email the finished videos (again, if already sent)."""
    setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    if to:
        ctx.job.inputs.notify_email = list(to)
    if outbox:
        ctx.job.options.email_outbox = True
    if not ctx.job.inputs.notify_email:
        typer.echo("No recipients: pass --to you@example.com", err=True)
        raise typer.Exit(1)
    ctx.workspace.notify_file.unlink(missing_ok=True)
    ctx.job.stages[StageName.NOTIFY] = StageStatus()
    ctx.save_job()
    execute(ctx, StageName.NOTIFY, False, set())


def _print_checks(spec: opt.JobSpec) -> bool:
    checks = run_checks(_settings(), spec.inputs, spec.options)
    for check in checks:
        typer.echo(check.line())
    return not blocking_failures(checks)


@app.command()
def doctor(
    inputs: list[Path] = opt.INPUTS,
    url: list[str] = opt.URL,
    email: list[str] = opt.EMAIL,
    email_outbox: bool = opt.EMAIL_OUTBOX,
    fake_generation: bool = opt.FAKE_GENERATION,
    fake_llm: bool = opt.FAKE_LLM,
    dry_run: bool = opt.DRY_RUN,
) -> None:
    """Check keys, tools, network and email settings for a run, without running it."""
    spec = opt.build_job_spec(
        _settings(), inputs, url, email, email_outbox, None, "", None, "social", None, [],
        fake_generation, fake_llm, dry_run, None, None, None,
    )  # fmt: skip
    if not _print_checks(spec):
        raise typer.Exit(1)
    typer.echo("Ready.")


@app.command()
def run(
    inputs: list[Path] = opt.INPUTS,
    job_id: str = opt.JOB_ID,
    url: list[str] = opt.URL,
    email: list[str] = opt.EMAIL,
    email_outbox: bool = opt.EMAIL_OUTBOX,
    floor_plan: Path | None = opt.FLOOR_PLAN,
    brief: str = opt.BRIEF,
    brief_file: Path | None = opt.BRIEF_FILE,
    fmt: str = opt.FORMAT,
    length: float | None = opt.LENGTH,
    aspect: list[str] = opt.ASPECT,
    fake_generation: bool = opt.FAKE_GENERATION,
    fake_llm: bool = opt.FAKE_LLM,
    dry_run: bool = opt.DRY_RUN,
    max_shots: int = opt.MAX_SHOTS,
    max_retries: int = opt.MAX_RETRIES,
    budget: float = opt.BUDGET,
    yes: bool = typer.Option(False, "--yes", help="Skip the cost confirmation"),
    skip_checks: bool = typer.Option(False, "--skip-checks", help="Don't run preflight checks"),
    verbose: bool = False,
) -> None:
    """Everything: checks, new job (or resume --job-id), all stages, email if --email."""
    setup_logging(verbose)
    settings = _settings()
    try:
        ctx = open_context(settings, job_id) if job_id else None
    except FileNotFoundError:
        ctx = None
    if ctx is None:
        spec = opt.build_job_spec(
            settings, inputs, url, email, email_outbox, floor_plan, brief, brief_file, fmt,
            length, aspect, fake_generation, fake_llm, dry_run, max_shots, max_retries, budget,
        )  # fmt: skip
        if not skip_checks and not _print_checks(spec):
            typer.echo("Fix the FAIL items above (or pass --skip-checks).", err=True)
            raise typer.Exit(1)
        ctx = open_context(settings, _create(spec, job_id))
    else:
        typer.echo(f"Resuming job {job_id}")
    execute(ctx, None, yes, set())
    if not ctx.job.options.dry_run:
        for aspect_ratio in ctx.job.inputs.brief.aspect_ratios:
            typer.echo(f"Output: {ctx.workspace.final_render_file(aspect_ratio)}")
        if ctx.job.inputs.notify_email:
            typer.echo(f"Emailed to: {', '.join(ctx.job.inputs.notify_email)}")


@app.command()
def status(job_id: str) -> None:
    """Show stage statuses and costs for a job."""
    ctx = open_context(_settings(), job_id)
    for name, st in ctx.job.stages.items():
        typer.echo(f"{name.value:<10} {st.state.value:<8} {st.summary or st.error or ''}")
    ledger = ctx.job.ledger
    typer.echo(
        f"cost: ${ledger.total():.2f} (claude ${ledger.total('claude'):.2f}, "
        f"higgsfield ${ledger.total('higgsfield'):.2f})"
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the HTTP API + minimal UI (Phase 6)."""
    import uvicorn

    uvicorn.run("tourgen.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
