"""Typer CLI for the OddsFox graph inference pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import pyarrow.parquet as pq
import typer

from oddsfox_graph.config import Settings
from oddsfox_graph.deterministic import build_deterministic_fragments_by_event
from oddsfox_graph.export import export_graph_artifacts
from oddsfox_graph.graphbuild import (
    build_graph_from_fragments,
    validate_exported_graph,
)
from oddsfox_graph.infer import infer_event_fragments, load_all_fragments
from oddsfox_graph.llm import LocalGraphLLM
from oddsfox_graph.reduce import load_semantic_markets, reduce_semantic_markets
from oddsfox_graph.reporting import build_inference_report
from oddsfox_graph.resolution import resolve_fragments
from oddsfox_graph.ontology import EdgeType
from oddsfox_graph.schema import CanonicalEdge, CanonicalNode

app = typer.Typer(
    name="oddsfox-graph",
    help="Local WC2026 Polymarket graph inference pipeline.",
    no_args_is_help=True,
)


def _settings_from_options(
    model_path: Optional[Path] = None,
    limit_events: Optional[int] = None,
    event_id: list[str] = [],
    resume: bool = True,
    minimum_confidence: float = 0.0,
) -> Settings:
    settings = Settings()
    if model_path is not None:
        settings.model_path = model_path
    if limit_events is not None:
        settings.limit_events = limit_events
    if event_id:
        settings.event_ids = list(event_id)
    settings.resume = resume
    settings.minimum_confidence = minimum_confidence
    return settings


@app.command()
def reduce() -> None:
    """Reduce hourly odds parquet to semantic market records."""
    settings = Settings()
    path = reduce_semantic_markets(settings)
    typer.echo(f"Wrote semantic markets to {path}")


@app.command()
def infer(
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
) -> None:
    """Infer graph fragments per event using local LLM."""
    settings = _settings_from_options(
        model_path=model_path,
        limit_events=limit_events,
        event_id=event_id,
        resume=resume,
    )
    markets = load_semantic_markets(settings.semantic_markets_path)
    llm = LocalGraphLLM(settings)
    results = infer_event_fragments(settings, markets, llm=llm)
    typer.echo(f"Inferred fragments for {len(results)} events")


@app.command()
def build(
    minimum_confidence: Annotated[
        float, typer.Option(help="Minimum edge confidence threshold")
    ] = 0.0,
) -> None:
    """Resolve entities, build graph, validate, and export artifacts."""
    settings = Settings()
    settings.minimum_confidence = minimum_confidence
    settings.ensure_dirs()

    markets = load_semantic_markets(settings.semantic_markets_path)
    deterministic = build_deterministic_fragments_by_event(markets)
    inferred = load_all_fragments(settings)

    det_fragments = list(deterministic.values())
    inf_fragments = list(inferred.values())

    resolution_det = resolve_fragments(det_fragments, settings, inference_method="deterministic")
    resolution_inf = resolve_fragments(inf_fragments, settings, inference_method="llm")

    # Merge resolution states
    merged_resolution = resolution_det
    for cid, node in resolution_inf.canonical_nodes.items():
        if cid in merged_resolution.canonical_nodes:
            existing = merged_resolution.canonical_nodes[cid]
            merged_resolution.canonical_nodes[cid] = existing.model_copy(
                update={
                    "confidence": max(existing.confidence, node.confidence),
                    "evidence_market_ids": sorted(
                        set(existing.evidence_market_ids) | set(node.evidence_market_ids)
                    ),
                    "aliases": sorted(set(existing.aliases) | set(node.aliases)),
                }
            )
        else:
            merged_resolution.canonical_nodes[cid] = node
    merged_resolution.local_to_canonical.update(resolution_inf.local_to_canonical)
    merged_resolution.unresolved.extend(resolution_inf.unresolved)
    for tier, count in resolution_inf.tier_counts.items():
        merged_resolution.tier_counts[tier] = (
            merged_resolution.tier_counts.get(tier, 0) + count
        )

    graph_result = build_graph_from_fragments(
        det_fragments + inf_fragments,
        merged_resolution,
        settings,
        fragment_methods=["deterministic"] * len(det_fragments)
        + ["llm"] * len(inf_fragments),
    )

    per_event_status: dict[str, str] = {}
    if settings.inference_report_path.exists():
        try:
            data = json.loads(settings.inference_report_path.read_text(encoding="utf-8"))
            per_event_status = data.get("per_event_status", {})
        except json.JSONDecodeError:
            per_event_status = {}

    report = build_inference_report(
        merged_resolution,
        graph_result,
        model_path=str(settings.model_path) if settings.model_path.exists() else None,
        per_event_status=per_event_status,
    )

    export_graph_artifacts(
        nodes=graph_result.nodes,
        edges=graph_result.edges,
        rejected_edges=graph_result.rejected_edges,
        unresolved=merged_resolution.unresolved,
        report=report,
        nodes_path=settings.nodes_path,
        edges_path=settings.edges_path,
        rejected_edges_path=settings.rejected_edges_path,
        unresolved_entities_path=settings.unresolved_entities_path,
        ontology_path=settings.ontology_path,
        inference_report_path=settings.inference_report_path,
    )
    typer.echo(
        f"Exported {len(graph_result.nodes)} nodes and {len(graph_result.edges)} edges"
    )


@app.command()
def validate() -> None:
    """Validate exported graph artifacts."""
    settings = Settings()
    nodes_table = pq.read_table(settings.nodes_path)
    edges_table = pq.read_table(settings.edges_path)

    nodes = [CanonicalNode(**row) for row in nodes_table.to_pylist()]
    edges = [
        CanonicalEdge(
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(row["edge_type"]),
            confidence=row["confidence"],
            evidence_market_ids=row["evidence_market_ids"],
            evidence_text=row.get("evidence_text", ""),
            inference_method=row.get("inference_method", "unknown"),
        )
        for row in edges_table.to_pylist()
    ]

    errors = validate_exported_graph(nodes, edges)
    if errors:
        typer.echo("Validation FAILED:")
        for error in errors:
            typer.echo(f"  - {error}")
        raise typer.Exit(code=1)

    typer.echo("Validation PASSED")


@app.command()
def run(
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
) -> None:
    """Run the full pipeline: reduce → infer → build → validate."""
    reduce()
    infer(
        model_path=model_path,
        limit_events=limit_events,
        event_id=event_id,
        resume=resume,
    )
    build(minimum_confidence=minimum_confidence)
    validate()


if __name__ == "__main__":
    app()
