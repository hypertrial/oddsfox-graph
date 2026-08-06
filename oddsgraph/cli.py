"""Typer CLI for the oddsgraph inference pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments, load_markets_for_infer
from oddsgraph.pipeline import run_build_and_export, validate_exported_artifacts
from oddsgraph.reduce import reduce_semantic_markets
from oddsgraph.reporting import load_inference_report

app = typer.Typer(
    name="oddsgraph",
    help="Local WC2026 Polymarket graph inference pipeline.",
    no_args_is_help=True,
)


def _base_settings(ctx: typer.Context) -> Settings:
    return ctx.obj if isinstance(ctx.obj, Settings) else Settings()


def _apply_infer_options(
    settings: Settings,
    model_path: Optional[Path] = None,
    limit_events: Optional[int] = None,
    event_id: list[str] = [],
    resume: bool = True,
    llm_backend: Optional[str] = None,
    server_url: Optional[str] = None,
    concurrency: Optional[int] = None,
    deterministic_topology: Optional[bool] = None,
) -> Settings:
    if model_path is not None:
        settings.model_path = model_path
    if limit_events is not None:
        settings.limit_events = limit_events
    if event_id:
        settings.event_ids = list(event_id)
    settings.resume = resume
    if llm_backend is not None:
        settings.llm_backend = llm_backend
    if server_url is not None:
        settings.server_base_url = server_url
    if concurrency is not None:
        settings.llm_concurrency = concurrency
    if deterministic_topology is not None:
        settings.deterministic_topology = deterministic_topology
    return settings


def _apply_build_options(
    settings: Settings,
    minimum_confidence: float = 0.0,
    official_bracket: Optional[bool] = None,
) -> Settings:
    settings.minimum_confidence = minimum_confidence
    if official_bracket is not None:
        settings.official_bracket = official_bracket
    return settings


def _echo_validation_errors(errors: list[str]) -> None:
    typer.echo("Validation FAILED:")
    for error in errors:
        typer.echo(f"  - {error}")


@app.callback()
def main(
    ctx: typer.Context,
    build_dir: Annotated[
        Optional[Path],
        typer.Option(help="Directory for build artifacts"),
    ] = None,
    data_dir: Annotated[
        Optional[Path],
        typer.Option(help="Directory containing source parquet files"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable INFO logging"),
    ] = False,
) -> None:
    """Shared options for all pipeline commands."""
    settings = Settings()
    if build_dir is not None:
        settings.configure_build_dir(build_dir)
    if data_dir is not None:
        settings.configure_data_dir(data_dir)
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )
    ctx.obj = settings


@app.command()
def reduce(ctx: typer.Context) -> None:
    """Reduce hourly odds parquet to semantic market records."""
    settings = _base_settings(ctx)
    path = reduce_semantic_markets(settings)
    typer.echo(f"Wrote semantic markets to {path}")


@app.command()
def infer(
    ctx: typer.Context,
    model_path: Annotated[
        Optional[Path], typer.Option(help="Path to GGUF model file")
    ] = None,
    limit_events: Annotated[
        Optional[int], typer.Option(help="Limit number of events to infer")
    ] = None,
    event_id: Annotated[
        list[str], typer.Option(help="Specific event IDs to infer")
    ] = [],
    resume: Annotated[bool, typer.Option(help="Skip events with existing fragments")] = True,
    llm_backend: Annotated[
        Optional[str],
        typer.Option(help="LLM backend: inprocess or server"),
    ] = None,
    server_url: Annotated[
        Optional[str],
        typer.Option(help="Base URL for llama-server when llm-backend=server"),
    ] = None,
    concurrency: Annotated[
        Optional[int],
        typer.Option(help="Concurrent LLM requests (server backend only)"),
    ] = None,
    deterministic_topology: Annotated[
        Optional[bool],
        typer.Option(
            "--deterministic-topology/--no-deterministic-topology",
            help="Extract TEAM/MATCH/GROUP/STAGE topology without LLM when possible",
        ),
    ] = None,
) -> None:
    """Infer graph fragments per event using local LLM."""
    settings = _apply_infer_options(
        _base_settings(ctx),
        model_path=model_path,
        limit_events=limit_events,
        event_id=event_id,
        resume=resume,
        llm_backend=llm_backend,
        server_url=server_url,
        concurrency=concurrency,
        deterministic_topology=deterministic_topology,
    )
    markets = load_markets_for_infer(settings)
    results = infer_event_fragments(settings, markets)
    report = load_inference_report(settings.inference_report_path)
    deterministic = sum(
        1 for status in report.per_event_status.values() if status == "deterministic"
    )
    typer.echo(
        f"Inferred fragments for {len(results)} events"
        + (f" ({deterministic} deterministic)" if deterministic else "")
    )


@app.command()
def build(
    ctx: typer.Context,
    minimum_confidence: Annotated[
        float, typer.Option(help="Minimum edge confidence threshold")
    ] = 0.0,
    official_bracket: Annotated[
        Optional[bool],
        typer.Option(
            "--official-bracket/--no-official-bracket",
            help="Inject curated WC2026 stage ladder and official MATCH bracket",
        ),
    ] = None,
) -> None:
    """Resolve entities, build graph, validate, and export artifacts."""
    settings = _apply_build_options(
        _base_settings(ctx),
        minimum_confidence=minimum_confidence,
        official_bracket=official_bracket,
    )
    result = run_build_and_export(settings)
    typer.echo(
        f"Exported {len(result.graph.nodes)} nodes and {len(result.graph.edges)} edges"
    )


@app.command()
def validate(ctx: typer.Context) -> None:
    """Validate exported graph artifacts."""
    settings = _base_settings(ctx)
    errors = validate_exported_artifacts(settings)
    if errors:
        _echo_validation_errors(errors)
        raise typer.Exit(code=1)

    typer.echo("Validation PASSED")


@app.command()
def run(
    ctx: typer.Context,
    model_path: Annotated[
        Optional[Path], typer.Option(help="Path to GGUF model file")
    ] = None,
    limit_events: Annotated[
        Optional[int], typer.Option(help="Limit number of events to infer")
    ] = None,
    event_id: Annotated[
        list[str], typer.Option(help="Specific event IDs to infer")
    ] = [],
    resume: Annotated[bool, typer.Option(help="Skip events with existing fragments")] = True,
    minimum_confidence: Annotated[
        float, typer.Option(help="Minimum edge confidence threshold")
    ] = 0.0,
    llm_backend: Annotated[
        Optional[str],
        typer.Option(help="LLM backend: inprocess or server"),
    ] = None,
    server_url: Annotated[
        Optional[str],
        typer.Option(help="Base URL for llama-server when llm-backend=server"),
    ] = None,
    concurrency: Annotated[
        Optional[int],
        typer.Option(help="Concurrent LLM requests (server backend only)"),
    ] = None,
    deterministic_topology: Annotated[
        Optional[bool],
        typer.Option(
            "--deterministic-topology/--no-deterministic-topology",
            help="Extract TEAM/MATCH/GROUP/STAGE topology without LLM when possible",
        ),
    ] = None,
    official_bracket: Annotated[
        Optional[bool],
        typer.Option(
            "--official-bracket/--no-official-bracket",
            help="Inject curated WC2026 stage ladder and official MATCH bracket",
        ),
    ] = None,
) -> None:
    """Run the full pipeline: reduce → infer → build → validate."""
    settings = _apply_build_options(
        _apply_infer_options(
            _base_settings(ctx),
            model_path=model_path,
            limit_events=limit_events,
            event_id=event_id,
            resume=resume,
            llm_backend=llm_backend,
            server_url=server_url,
            concurrency=concurrency,
            deterministic_topology=deterministic_topology,
        ),
        minimum_confidence=minimum_confidence,
        official_bracket=official_bracket,
    )
    reduce_semantic_markets(settings)
    markets = load_markets_for_infer(settings)
    # Lazy LLM load inside infer_event_fragments only when residual chunks remain.
    infer_event_fragments(settings, markets)
    # Reuse the same market list so --event-id/--limit-events stay consistent.
    run_build_and_export(settings, markets=markets)
    errors = validate_exported_artifacts(settings)
    if errors:
        _echo_validation_errors(errors)
        raise typer.Exit(code=1)
    typer.echo("Pipeline completed successfully")


if __name__ == "__main__":
    app()
