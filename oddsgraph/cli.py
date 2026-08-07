"""Typer CLI for the oddsgraph inference pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import pyarrow.parquet as pq
import typer

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments, load_markets_for_infer
from oddsgraph.ontology import EdgeType
from oddsgraph.pipeline import run_build_and_export, validate_exported_artifacts
from oddsgraph.reduce import reduce_semantic_markets
from oddsgraph.reporting import load_inference_report
from oddsgraph import cli_options as opts

app = typer.Typer(
    name="oddsgraph",
    help="Local Logical Knowledge Graph Compiler for Polymarket WC2026.",
    no_args_is_help=True,
)


def _base_settings(ctx: typer.Context) -> Settings:
    return ctx.obj if isinstance(ctx.obj, Settings) else Settings()


def _apply_infer_options(
    settings: Settings,
    model_path: Optional[Path] = None,
    mlx_model_path: Optional[Path] = None,
    limit_events: Optional[int] = None,
    event_id: list[str] = [],
    resume: bool = True,
    llm_backend: Optional[str] = None,
    server_url: Optional[str] = None,
    concurrency: Optional[int] = None,
    deterministic_topology: Optional[bool] = None,
    verify_deterministic: Optional[bool] = None,
    few_shot: Optional[bool] = None,
    chunk_token_budget: Optional[int] = None,
    chunk_output_token_budget: Optional[int] = None,
    max_markets_per_chunk: Optional[int] = None,
) -> Settings:
    if model_path is not None:
        settings.model_path = model_path
    if mlx_model_path is not None:
        settings.mlx_model_path = mlx_model_path
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
    if verify_deterministic is not None:
        settings.verify_deterministic = verify_deterministic
    if few_shot is not None:
        settings.use_few_shot_exemplars = few_shot
    if chunk_token_budget is not None:
        settings.chunk_token_budget = chunk_token_budget
    if chunk_output_token_budget is not None:
        settings.chunk_output_token_budget = chunk_output_token_budget
    if max_markets_per_chunk is not None:
        settings.max_markets_per_chunk = max_markets_per_chunk
    return settings


def _apply_build_options(
    settings: Settings,
    minimum_confidence: float = 0.0,
    official_bracket: Optional[bool] = None,
    compile_propositions: Optional[bool] = None,
    apply_rules: Optional[bool] = None,
) -> Settings:
    settings.minimum_confidence = minimum_confidence
    if official_bracket is not None:
        settings.official_bracket = official_bracket
    if compile_propositions is not None:
        settings.compile_propositions = compile_propositions
    if apply_rules is not None:
        settings.apply_rules = apply_rules
    return settings


def _echo_validation_errors(errors: list[str]) -> None:
    typer.echo("Validation FAILED:")
    for error in errors:
        typer.echo(f"  - {error}")


PropositionsOpt = Annotated[
    Optional[bool],
    typer.Option(
        "--propositions/--no-propositions",
        help="Compile formal propositions onto OUTCOME nodes",
    ),
]
ReasoningOpt = Annotated[
    Optional[bool],
    typer.Option(
        "--reasoning/--no-reasoning",
        help="Apply deterministic logical rules over compiled propositions",
    ),
]


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
    model_path: opts.ModelPathOpt = None,
    mlx_model_path: opts.MlxModelPathOpt = None,
    limit_events: opts.LimitEventsOpt = None,
    event_id: opts.EventIdOpt = [],
    resume: opts.ResumeOpt = True,
    llm_backend: opts.LlmBackendOpt = None,
    server_url: opts.ServerUrlOpt = None,
    concurrency: opts.ConcurrencyOpt = None,
    deterministic_topology: opts.DeterministicTopologyOpt = None,
    verify_deterministic: opts.VerifyDeterministicOpt = None,
    few_shot: opts.FewShotOpt = None,
    chunk_token_budget: opts.ChunkTokenBudgetOpt = None,
    chunk_output_token_budget: opts.ChunkOutputTokenBudgetOpt = None,
    max_markets_per_chunk: opts.MaxMarketsPerChunkOpt = None,
) -> None:
    """Infer graph fragments per event using local LLM."""
    settings = _apply_infer_options(
        _base_settings(ctx),
        model_path=model_path,
        mlx_model_path=mlx_model_path,
        limit_events=limit_events,
        event_id=event_id,
        resume=resume,
        llm_backend=llm_backend,
        server_url=server_url,
        concurrency=concurrency,
        deterministic_topology=deterministic_topology,
        verify_deterministic=verify_deterministic,
        few_shot=few_shot,
        chunk_token_budget=chunk_token_budget,
        chunk_output_token_budget=chunk_output_token_budget,
        max_markets_per_chunk=max_markets_per_chunk,
    )
    markets = load_markets_for_infer(settings)
    results = infer_event_fragments(settings, markets)
    report = load_inference_report(settings.inference_report_path)
    deterministic = sum(
        1
        for status in report.per_event_status.values()
        if status
        in {"deterministic", "deterministic_verified", "deterministic_corrected"}
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
    propositions: PropositionsOpt = None,
    reasoning: ReasoningOpt = None,
) -> None:
    """Resolve entities, build graph, validate, and export artifacts."""
    settings = _apply_build_options(
        _base_settings(ctx),
        minimum_confidence=minimum_confidence,
        official_bracket=official_bracket,
        compile_propositions=propositions,
        apply_rules=reasoning,
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


@app.command("closure")
def implies_closure(ctx: typer.Context) -> None:
    """Compute on-demand transitive IMPLIES closure from exported edges."""
    settings = _base_settings(ctx)
    if not settings.edges_path.exists():
        typer.echo(
            f"No exported edges found under {settings.build_dir}. "
            "Run `oddsgraph build` first."
        )
        raise typer.Exit(code=1)

    from oddsgraph.closure import compute_implies_closure
    from oddsgraph.export import EDGE_SCHEMA, write_parquet
    from oddsgraph.schema import CanonicalEdge

    edges_table = pq.read_table(settings.edges_path)
    edges = [
        CanonicalEdge(
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(row["edge_type"]),
            confidence=row["confidence"],
            evidence_market_ids=row["evidence_market_ids"],
            evidence_text=row.get("evidence_text", ""),
            inference_method=row.get("inference_method", "unknown"),
            derivation_type=row.get("derivation_type", "extraction"),
            rule_id=row.get("rule_id"),
            rule_version=row.get("rule_version"),
            premises=row.get("premises"),
        )
        for row in edges_table.to_pylist()
    ]
    closure_edges = compute_implies_closure(edges)
    settings.ensure_dirs()
    write_parquet(
        settings.implies_closure_path,
        [
            {
                "source_id": e.source_id,
                "target_id": e.target_id,
                "edge_type": e.edge_type.value,
                "confidence": e.confidence,
                "evidence_market_ids": e.evidence_market_ids,
                "evidence_text": e.evidence_text,
                "inference_method": e.inference_method,
                "derivation_type": e.derivation_type,
                "rule_id": e.rule_id,
                "rule_version": e.rule_version,
                "premises": e.premises,
            }
            for e in closure_edges
        ],
        EDGE_SCHEMA,
    )
    typer.echo(
        f"Wrote {len(closure_edges)} transitive IMPLIES edges to {settings.implies_closure_path}"
    )


@app.command()
def explore(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option(help="Bind host (local-only by default)"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(help="Port for the explorer server"),
    ] = 8050,
    debug: Annotated[
        bool,
        typer.Option(help="Enable Dash debug/hot-reload"),
    ] = False,
) -> None:
    """Launch a local, read-only graph explorer over exported artifacts."""
    settings = _base_settings(ctx)
    if not settings.nodes_path.exists() or not settings.edges_path.exists():
        typer.echo(
            f"No exported graph found under {settings.build_dir}. "
            "Run `oddsgraph build` first."
        )
        raise typer.Exit(code=1)
    try:
        from oddsgraph.explorer.runner import run_explorer
    except ImportError as exc:
        typer.echo(
            "Explorer dependencies are missing. Install with:\n"
            "  uv sync --frozen --extra explore\n"
            f"Import error: {exc}"
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Starting explorer at http://{host}:{port} (build={settings.build_dir})")
    run_explorer(settings, host=host, port=port, debug=debug)


@app.command()
def run(
    ctx: typer.Context,
    model_path: opts.ModelPathOpt = None,
    mlx_model_path: opts.MlxModelPathOpt = None,
    limit_events: opts.LimitEventsOpt = None,
    event_id: opts.EventIdOpt = [],
    resume: opts.ResumeOpt = True,
    minimum_confidence: Annotated[
        float, typer.Option(help="Minimum edge confidence threshold")
    ] = 0.0,
    llm_backend: opts.LlmBackendOpt = None,
    server_url: opts.ServerUrlOpt = None,
    concurrency: opts.ConcurrencyOpt = None,
    deterministic_topology: opts.DeterministicTopologyOpt = None,
    verify_deterministic: opts.VerifyDeterministicOpt = None,
    few_shot: opts.FewShotOpt = None,
    chunk_token_budget: opts.ChunkTokenBudgetOpt = None,
    chunk_output_token_budget: opts.ChunkOutputTokenBudgetOpt = None,
    max_markets_per_chunk: opts.MaxMarketsPerChunkOpt = None,
    official_bracket: Annotated[
        Optional[bool],
        typer.Option(
            "--official-bracket/--no-official-bracket",
            help="Inject curated WC2026 stage ladder and official MATCH bracket",
        ),
    ] = None,
    propositions: PropositionsOpt = None,
    reasoning: ReasoningOpt = None,
) -> None:
    """Run the full pipeline: reduce → infer → build → validate."""
    settings = _apply_build_options(
        _apply_infer_options(
            _base_settings(ctx),
            model_path=model_path,
            mlx_model_path=mlx_model_path,
            limit_events=limit_events,
            event_id=event_id,
            resume=resume,
            llm_backend=llm_backend,
            server_url=server_url,
            concurrency=concurrency,
            deterministic_topology=deterministic_topology,
            verify_deterministic=verify_deterministic,
            few_shot=few_shot,
            chunk_token_budget=chunk_token_budget,
            chunk_output_token_budget=chunk_output_token_budget,
            max_markets_per_chunk=max_markets_per_chunk,
        ),
        minimum_confidence=minimum_confidence,
        official_bracket=official_bracket,
        compile_propositions=propositions,
        apply_rules=reasoning,
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
