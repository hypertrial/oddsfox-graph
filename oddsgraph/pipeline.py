"""Shared build pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow.parquet as pq

from oddsgraph.bracket import build_official_bracket_fragment
from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.export import export_graph_artifacts
from oddsgraph.graphbuild import (
    GraphBuildResult,
    accept_edges,
    build_graph_from_fragments,
    dedupe_edges,
    has_implies_cycle,
    validate_exported_graph,
)
from oddsgraph.infer import load_all_fragments
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.propositions import compile_propositions
from oddsgraph.reduce import load_semantic_markets
from oddsgraph.reporting import build_inference_report, load_inference_report
from oddsgraph.resolution import ResolutionState, resolve_fragments
from oddsgraph.rules import apply_rules
from oddsgraph.schema import (
    CanonicalEdge,
    CanonicalNode,
    GraphFragment,
    InferenceReport,
    Proposition,
    RejectedEdge,
    SemanticMarket,
)


@dataclass
class BuildPipelineResult:
    resolution: ResolutionState
    graph: GraphBuildResult
    report: InferenceReport
    propositions: dict[str, Proposition] | None = None


def _attach_propositions(
    nodes: list[CanonicalNode],
    propositions: dict[str, Proposition],
) -> list[CanonicalNode]:
    if not propositions:
        return nodes
    updated: list[CanonicalNode] = []
    for node in nodes:
        prop = propositions.get(node.canonical_id)
        if prop is not None and node.type == NodeType.OUTCOME:
            updated.append(node.model_copy(update={"proposition": prop}))
        else:
            updated.append(node)
    return updated


def build_pipeline_from_markets(
    settings: Settings,
    markets: list[SemanticMarket],
    inferred_fragments: dict[str, GraphFragment] | None = None,
    *,
    verified_event_ids: set[str] | None = None,
    topology_fragments: dict[str, GraphFragment] | None = None,
) -> BuildPipelineResult:
    allowed_event_ids = {m.event_id for m in markets}
    verified_ids = {
        eid for eid in (verified_event_ids or set()) if eid in allowed_event_ids
    }
    preloaded_topology = {
        eid: fragment
        for eid, fragment in (topology_fragments or {}).items()
        if eid in allowed_event_ids and eid not in verified_ids
    }
    deterministic = build_deterministic_fragments_by_event(
        markets,
        include_topology=settings.deterministic_topology,
        competition_label=settings.competition_label,
        skip_topology_event_ids=verified_ids,
        topology_fragments=preloaded_topology,
    )
    inferred = {
        eid: fragment
        for eid, fragment in (inferred_fragments or {}).items()
        if eid in allowed_event_ids
    }
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

    compiled_propositions: dict[str, Proposition] = {}
    if settings.compile_propositions:
        compilation = compile_propositions(
            markets, competition_label=settings.competition_label
        )
        if compilation.fragment.nodes or compilation.fragment.edges:
            all_fragments.append(compilation.fragment)
            inference_methods.append("proposition_compiler")
        compiled_propositions = compilation.propositions

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

    if compiled_propositions:
        graph.nodes = _attach_propositions(graph.nodes, compiled_propositions)
        # Keep resolution state in sync for downstream consumers.
        for node in graph.nodes:
            resolution.canonical_nodes[node.canonical_id] = node

    if settings.apply_rules and compiled_propositions:
        rule_edges = apply_rules(graph.nodes)
        if rule_edges:
            node_types = {n.canonical_id: n.type for n in graph.nodes}
            accepted, rejected = accept_edges(rule_edges, node_types, settings)
            merged = dedupe_edges(graph.edges + accepted)
            # On cycle, drop only rule-engine IMPLIES so fragment edges stay.
            # ``reject_implies_cycle`` (used in graph build) drops every IMPLIES.
            implies = [e for e in merged if e.edge_type == EdgeType.IMPLIES]
            if has_implies_cycle(implies):
                non_implies_rules = [
                    e for e in accepted if e.edge_type != EdgeType.IMPLIES
                ]
                graph.edges = dedupe_edges(graph.edges + non_implies_rules)
                for edge in accepted:
                    if edge.edge_type == EdgeType.IMPLIES:
                        graph.rejected_edges.append(
                            RejectedEdge(
                                **edge.model_dump(),
                                rejection_reason="implies_cycle",
                            )
                        )
            else:
                graph.edges = merged
            graph.rejected_edges.extend(rejected)

    existing_report = load_inference_report(settings.inference_report_path)
    report = build_inference_report(
        resolution,
        graph,
        model_path=str(settings.model_path) if settings.model_path.exists() else None,
        per_event_status=existing_report.per_event_status,
    )
    return BuildPipelineResult(
        resolution=resolution,
        graph=graph,
        report=report,
        propositions=compiled_propositions or None,
    )


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
        topology_fragments=loaded.topology_fragments,
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

    nodes: list[CanonicalNode] = []
    for row in nodes_table.to_pylist():
        prop_json = row.get("proposition_json")
        prop = Proposition.model_validate_json(prop_json) if prop_json else None
        nodes.append(
            CanonicalNode(
                canonical_id=row["canonical_id"],
                type=NodeType(row["type"]),
                label=row["label"],
                aliases=row.get("aliases") or [],
                confidence=row["confidence"],
                evidence_market_ids=row.get("evidence_market_ids") or [],
                resolution_method=row.get("resolution_method", "unresolved"),
                inference_method=row.get("inference_method", "unknown"),
                proposition=prop,
            )
        )

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
    return validate_exported_graph(nodes, edges)
