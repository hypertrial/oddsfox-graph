"""Shared build pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow.parquet as pq

from oddsgraph.bracket import build_official_bracket_fragment
from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.export import export_graph_artifacts
from oddsgraph.graphbuild import GraphBuildResult, build_graph_from_fragments, validate_exported_graph
from oddsgraph.infer import load_all_fragments
from oddsgraph.ontology import EdgeType
from oddsgraph.reduce import load_semantic_markets
from oddsgraph.reporting import build_inference_report, load_inference_report
from oddsgraph.resolution import ResolutionState, resolve_fragments
from oddsgraph.schema import CanonicalEdge, CanonicalNode, GraphFragment, InferenceReport, SemanticMarket


@dataclass
class BuildPipelineResult:
    resolution: ResolutionState
    graph: GraphBuildResult
    report: InferenceReport


def build_pipeline_from_markets(
    settings: Settings,
    markets: list[SemanticMarket],
    inferred_fragments: dict[str, GraphFragment] | None = None,
    *,
    verified_event_ids: set[str] | None = None,
) -> BuildPipelineResult:
    verified_ids = verified_event_ids or set()
    deterministic = build_deterministic_fragments_by_event(
        markets,
        include_topology=settings.deterministic_topology,
        competition_label=settings.competition_label,
        skip_topology_event_ids=verified_ids,
    )
    inferred = inferred_fragments or {}
    det_fragments = list(deterministic.values())
    inferred_items = list(inferred.items())
    inf_fragments = [fragment for _, fragment in inferred_items]
    all_fragments = det_fragments + inf_fragments
    inference_methods = ["deterministic"] * len(det_fragments) + [
        "verified" if event_id in verified_ids else "llm"
        for event_id, _ in inferred_items
    ]

    if settings.official_bracket:
        bracket = build_official_bracket_fragment(settings.competition_label)
        all_fragments.append(bracket)
        inference_methods.append("official_bracket")

    resolution = resolve_fragments(
        all_fragments,
        settings,
        inference_methods=inference_methods,
    )
    graph = build_graph_from_fragments(
        all_fragments,
        resolution,
        settings,
        fragment_methods=inference_methods,
    )
    existing_report = load_inference_report(settings.inference_report_path)
    report = build_inference_report(
        resolution,
        graph,
        model_path=str(settings.model_path) if settings.model_path.exists() else None,
        per_event_status=existing_report.per_event_status,
    )
    return BuildPipelineResult(resolution=resolution, graph=graph, report=report)


def run_build_and_export(
    settings: Settings,
    markets: list[SemanticMarket] | None = None,
) -> BuildPipelineResult:
    settings.ensure_dirs()
    if markets is None:
        markets = load_semantic_markets(settings.semantic_markets_path)
    loaded = load_all_fragments(settings)
    result = build_pipeline_from_markets(
        settings,
        markets,
        loaded.fragments,
        verified_event_ids=loaded.verified_event_ids,
    )

    export_graph_artifacts(
        nodes=result.graph.nodes,
        edges=result.graph.edges,
        rejected_edges=result.graph.rejected_edges,
        report=result.report,
        nodes_path=settings.nodes_path,
        edges_path=settings.edges_path,
        rejected_edges_path=settings.rejected_edges_path,
        ontology_path=settings.ontology_path,
        inference_report_path=settings.inference_report_path,
    )
    return result


def validate_exported_artifacts(settings: Settings) -> list[str]:
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
    return validate_exported_graph(nodes, edges)
