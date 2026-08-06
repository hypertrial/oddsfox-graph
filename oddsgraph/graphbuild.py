"""Graph construction and validation with rustworkx."""

from __future__ import annotations

from dataclasses import dataclass, field

import rustworkx as rx

from oddsgraph.config import Settings
from oddsgraph.ontology import (
    EdgeType,
    is_allowed_edge,
    PROGRESSION_EDGE_TYPES,
)
from oddsgraph.resolution import ResolutionState
from oddsgraph.schema import CanonicalEdge, CanonicalNode, Edge, GraphFragment, RejectedEdge


@dataclass
class GraphBuildResult:
    nodes: list[CanonicalNode] = field(default_factory=list)
    edges: list[CanonicalEdge] = field(default_factory=list)
    rejected_edges: list[RejectedEdge] = field(default_factory=list)


def _dedupe_edges(edges: list[CanonicalEdge]) -> list[CanonicalEdge]:
    merged: dict[tuple[str, str, str], CanonicalEdge] = {}
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.edge_type.value)
        existing = merged.get(key)
        if existing is None:
            merged[key] = edge
            continue
        merged_evidence = sorted(
            set(existing.evidence_market_ids) | set(edge.evidence_market_ids)
        )
        evidence_text = existing.evidence_text or edge.evidence_text
        merged[key] = existing.model_copy(
            update={
                "confidence": max(existing.confidence, edge.confidence),
                "evidence_market_ids": merged_evidence,
                "evidence_text": evidence_text,
            }
        )
    return list(merged.values())


def _has_progression_cycle(edges: list[CanonicalEdge]) -> bool:
    if not edges:
        return False

    graph = rx.PyDiGraph()
    node_index: dict[str, int] = {}

    def idx(node_id: str) -> int:
        if node_id not in node_index:
            node_index[node_id] = graph.add_node(node_id)
        return node_index[node_id]

    for edge in edges:
        if edge.edge_type not in PROGRESSION_EDGE_TYPES:
            continue
        graph.add_edge(idx(edge.source_id), idx(edge.target_id), edge)

    if graph.num_nodes() == 0:
        return False

    return not rx.is_directed_acyclic_graph(graph)


def build_graph_from_fragments(
    fragments: list[GraphFragment],
    resolution_state: ResolutionState,
    settings: Settings,
    fragment_methods: list[str] | None = None,
) -> GraphBuildResult:
    inference_methods = fragment_methods or []
    result = GraphBuildResult(nodes=list(resolution_state.canonical_nodes.values()))

    node_types = {n.canonical_id: n.type for n in result.nodes}
    raw_edges: list[CanonicalEdge] = []

    for idx, fragment in enumerate(fragments):
        method = inference_methods[idx] if idx < len(inference_methods) else "unknown"
        for edge in fragment.edges:
            source_id = resolution_state.local_to_canonical.get(edge.source)
            target_id = resolution_state.local_to_canonical.get(edge.target)
            if not source_id or not target_id:
                continue
            if source_id.startswith("unresolved:") or target_id.startswith("unresolved:"):
                continue
            raw_edges.append(
                CanonicalEdge(
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=edge.type,
                    confidence=edge.confidence,
                    evidence_market_ids=list(edge.evidence_market_ids),
                    evidence_text=edge.evidence_text,
                    inference_method=method,
                )
            )

    deduped = _dedupe_edges(raw_edges)
    accepted: list[CanonicalEdge] = []
    rejected: list[RejectedEdge] = []

    for edge in deduped:
        if edge.confidence < settings.minimum_confidence:
            rejected.append(
                RejectedEdge(
                    **edge.model_dump(),
                    rejection_reason="below_minimum_confidence",
                )
            )
            continue
        if edge.confidence < 0 or edge.confidence > 1:
            rejected.append(
                RejectedEdge(**edge.model_dump(), rejection_reason="invalid_confidence")
            )
            continue
        if not edge.evidence_market_ids:
            rejected.append(
                RejectedEdge(**edge.model_dump(), rejection_reason="missing_evidence")
            )
            continue
        source_type = node_types.get(edge.source_id)
        target_type = node_types.get(edge.target_id)
        if source_type is None or target_type is None:
            rejected.append(
                RejectedEdge(**edge.model_dump(), rejection_reason="missing_endpoint")
            )
            continue
        if not is_allowed_edge(edge.edge_type, source_type, target_type):
            rejected.append(
                RejectedEdge(**edge.model_dump(), rejection_reason="invalid_pattern")
            )
            continue
        accepted.append(edge)

    progression_edges = [e for e in accepted if e.edge_type in PROGRESSION_EDGE_TYPES]
    if _has_progression_cycle(progression_edges):
        new_accepted: list[CanonicalEdge] = []
        for edge in accepted:
            if edge.edge_type in PROGRESSION_EDGE_TYPES:
                rejected.append(
                    RejectedEdge(**edge.model_dump(), rejection_reason="progression_cycle")
                )
            else:
                new_accepted.append(edge)
        accepted = new_accepted

    result.edges = accepted
    result.rejected_edges = rejected
    return result


def validate_exported_graph(
    nodes: list[CanonicalNode],
    edges: list[CanonicalEdge],
) -> list[str]:
    errors: list[str] = []
    node_ids = {n.canonical_id for n in nodes}
    if len(node_ids) != len(nodes):
        errors.append("duplicate node ids")

    node_types = {n.canonical_id: n.type for n in nodes}
    for edge in edges:
        if edge.source_id not in node_ids:
            errors.append(f"missing source endpoint: {edge.source_id}")
        if edge.target_id not in node_ids:
            errors.append(f"missing target endpoint: {edge.target_id}")
        if not edge.evidence_market_ids:
            errors.append(f"missing evidence: {edge.source_id}->{edge.target_id}")
        if edge.confidence < 0 or edge.confidence > 1:
            errors.append(f"invalid confidence: {edge.source_id}->{edge.target_id}")
        source_type = node_types.get(edge.source_id)
        target_type = node_types.get(edge.target_id)
        if source_type and target_type and not is_allowed_edge(
            edge.edge_type, source_type, target_type
        ):
            errors.append(f"invalid pattern: {edge.source_id}->{edge.target_id}")

    progression = [e for e in edges if e.edge_type in PROGRESSION_EDGE_TYPES]
    if _has_progression_cycle(progression):
        errors.append("progression cycle detected")

    return errors
