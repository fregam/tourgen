"""
FILE: tourgen/cli.py
ROLE: Typer entrypoint — the primary interface. Commands: new, ingest, analyze, plan,
      estimate, generate, qa, render, run, status, serve.

HOW IT WORKS:
  - `new` creates a job workspace (core/pipeline.create_job); the stage commands
    run the pipeline up to a given stage (resumable, already-done stages skip).
  - `run` = new (if needed) + every stage. Budget confirmation: generation stops
    with ConfirmationRequired unless --yes or the user confirms interactively.

CONNECTS TO:
  - core/pipeline.py, core/costs.py, config.py, api/app.py (serve)

INVARIANTS / GOTCHAS:
  - Never put pipeline logic here; commands only translate flags into JobOptions
    and call core/pipeline.py.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import typer

from tourgen.config import Settings
from tourgen.core.context import StageContext
from tourgen.core.costs import BudgetExceeded, ConfirmationRequired, estimate_job
from tourgen.core.pipeline import attach_job_log, create_job, open_context, run_pipeline
from tourgen.ingest.listing_url import RIGHTS_REMINDER
from tourgen.models.job import Brief, JobInputs, JobOptions, StageName, StageState
from tourgen.models.plan import TourPlan

app = typer.Typer(
    help="AI house tour generator (Claude + Higgsfield + ffmpeg).", no_args_is_help=True
)


def _settings() -> Settings:
    return Settings()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _progress(name: StageName, state: StageState, msg: str | None) -> None:
    icon = {StageState.RUNNING: "…", StageState.DONE: "✓", StageState.FAILED: "✗"}.get(state, " ")
    typer.echo(f" {icon} {name.value:<10} {msg or ''}")


def _build_brief(
    brief_text: str, brief_file: Path | None, fmt: str, length: float | None, aspects: list[str]
) -> Brief:
    data: dict[str, object] = json.loads(brief_file.read_text()) if brief_file else {}
    brief = Brief.model_validate(data)
    if brief_text:
        brief.text = brief_text
    brief.format = fmt or brief.format
    if length:
        brief.target_length_s = length
    if aspects:
        brief.aspect_ratios = aspects
    return brief


def _execute(ctx: StageContext, until: StageName | None, yes: bool, force: set[StageName]) -> None:
    handler = attach_job_log(ctx.workspace)
    try:
        while True:
            try:
                run_pipeline(ctx, until=until, force=force, on_progress=_progress)
                return
            except ConfirmationRequired as exc:
                if not (yes or typer.confirm(f"{exc}. Proceed?", default=False)):
                    raise typer.Exit(1) from exc
                ctx.job.options.approved_cost_usd = exc.estimate_usd
                ctx.save_job()
                force = set()
            except BudgetExceeded as exc:
                typer.echo(f"Budget: {exc}", err=True)
                raise typer.Exit(2) from exc
    finally:
        logging.getLogger("tourgen").removeHandler(handler)


@app.command()
def new(
    inputs: list[Path] = typer.Argument(None, help="Photo/video files or folders"),
    job_id: str = typer.Option(None, "--job-id", help="Defaults to a timestamp"),
    url: list[str] = typer.Option([], "--url", help="Listing URL(s) to scrape"),
    floor_plan: Path | None = typer.Option(None, "--floor-plan"),
    brief: str = typer.Option("", "--brief", help="Free-text brief (audience, tone, …)"),
    brief_file: Path | None = typer.Option(
        None, "--brief-file", help="JSON matching models.job.Brief"
    ),
    fmt: str = typer.Option("social", "--format", help="social (45–75s) | full (90–150s)"),
    length: float | None = typer.Option(None, "--length", help="Target length in seconds"),
    aspect: list[str] = typer.Option([], "--aspect", help="Repeatable, e.g. --aspect 16:9"),
    fake_generation: bool = typer.Option(False, "--fake-generation"),
    fake_llm: bool = typer.Option(False, "--fake-llm", help="Offline filename-based fake Claude"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop after compiling payloads"),
    max_shots: int = typer.Option(None, "--max-shots"),
    max_retries: int = typer.Option(None, "--max-retries"),
    budget: float = typer.Option(None, "--budget", help="Hard cap in USD"),
) -> str:
    """Create a job workspace."""
    settings = _settings()
    if url:
        typer.echo(RIGHTS_REMINDER)
    job_id = job_id or datetime.now(UTC).strftime("job-%Y%m%d-%H%M%S")
    job_inputs = JobInputs(
        paths=[str(p.resolve()) for p in (inputs or [])],
        urls=url,
        floor_plan=str(floor_plan.resolve()) if floor_plan else None,
        brief=_build_brief(brief, brief_file, fmt, length, aspect),
    )
    options = JobOptions(
        fake_generation=fake_generation,
        fake_llm=fake_llm,
        dry_run=dry_run,
        max_shots=max_shots or settings.max_shots,
        max_retries=settings.max_retries if max_retries is None else max_retries,
        budget_usd=budget or settings.budget_usd,
    )
    workspace = create_job(settings, job_id, job_inputs, options)
    typer.echo(f"Created job {job_id} at {workspace.root}")
    return job_id


def _stage_command(job_id: str, until: StageName, yes: bool, force: bool, verbose: bool) -> None:
    _setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    _execute(ctx, until, yes, {until} if force else set())


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
    _setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    if budget:
        ctx.job.options.budget_usd = budget
    ctx.job.options.dry_run = False
    _execute(ctx, StageName.GENERATE, yes, set())


@app.command()
def qa(job_id: str, yes: bool = False, verbose: bool = False) -> None:
    """Stage 7: QA every clip, retry or fall back."""
    _stage_command(job_id, StageName.QA, yes, False, verbose)


@app.command()
def render(job_id: str, yes: bool = False, force: bool = False, verbose: bool = False) -> None:
    """Stages 8–9: assemble final videos, manifest and report."""
    _setup_logging(verbose)
    ctx = open_context(_settings(), job_id)
    _execute(ctx, StageName.DELIVER, yes, {StageName.ASSEMBLE} if force else set())


@app.command()
def run(
    inputs: list[Path] = typer.Argument(None, help="Photo/video files or folders"),
    job_id: str = typer.Option(None, "--job-id"),
    url: list[str] = typer.Option([], "--url"),
    floor_plan: Path | None = typer.Option(None, "--floor-plan"),
    brief: str = typer.Option("", "--brief"),
    brief_file: Path | None = typer.Option(None, "--brief-file"),
    fmt: str = typer.Option("social", "--format"),
    length: float | None = typer.Option(None, "--length"),
    aspect: list[str] = typer.Option([], "--aspect"),
    fake_generation: bool = typer.Option(False, "--fake-generation"),
    fake_llm: bool = typer.Option(False, "--fake-llm"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_shots: int = typer.Option(None, "--max-shots"),
    max_retries: int = typer.Option(None, "--max-retries"),
    budget: float = typer.Option(None, "--budget"),
    yes: bool = typer.Option(False, "--yes"),
    verbose: bool = False,
) -> None:
    """Full pipeline: create the job (or resume it if --job-id exists) and run every stage."""
    _setup_logging(verbose)
    settings = _settings()
    try:
        ctx = open_context(settings, job_id) if job_id else None
    except FileNotFoundError:
        ctx = None
    if ctx is None:
        job_id = new(
            inputs, job_id, url, floor_plan, brief, brief_file, fmt, length, aspect,
            fake_generation, fake_llm, dry_run, max_shots, max_retries, budget,
        )  # fmt: skip
        ctx = open_context(settings, job_id)
    else:
        typer.echo(f"Resuming job {job_id}")
    _execute(ctx, None, yes, set())
    if not ctx.job.options.dry_run:
        for aspect_ratio in ctx.job.inputs.brief.aspect_ratios:
            typer.echo(f"Output: {ctx.workspace.final_render_file(aspect_ratio)}")


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
