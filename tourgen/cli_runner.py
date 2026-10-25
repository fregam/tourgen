"""
FILE: tourgen/cli_runner.py
ROLE: Runs the pipeline on behalf of CLI commands: console logging, per-stage progress
      lines, and the interactive cost-confirmation / budget loop.

HOW IT WORKS:
  - execute() calls core/pipeline.run_pipeline(); on ConfirmationRequired it asks
    (or accepts --yes), records the approved amount in job.json and resumes.

CONNECTS TO:
  - cli.py; core/pipeline.py, core/costs.py

INVARIANTS / GOTCHAS:
  - Attaches the per-job log file for the duration of the run only.
"""

from __future__ import annotations

import logging

import typer

from tourgen.core.context import StageContext
from tourgen.core.costs import BudgetExceeded, ConfirmationRequired
from tourgen.core.pipeline import attach_job_log, run_pipeline
from tourgen.models.job import StageName, StageState


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _progress(name: StageName, state: StageState, msg: str | None) -> None:
    icon = {StageState.RUNNING: "…", StageState.DONE: "✓", StageState.FAILED: "✗"}.get(state, " ")
    typer.echo(f" {icon} {name.value:<10} {msg or ''}")


def execute(ctx: StageContext, until: StageName | None, yes: bool, force: set[StageName]) -> None:
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
