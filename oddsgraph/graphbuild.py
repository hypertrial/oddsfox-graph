"""Graph construction and validation with rustworkx."""

from __future__ import annotations

from dataclasses import dataclass, field

import rustworkx as rx

from oddsgraph.config import Settings
from oddsgraph.ontology import (
    is_allowed_edge,
    PROGRESSION_EDGE_TYPES,
    EdgeType,
)
from oddsgraph.resolution import ResolutionState
from oddsgraph.schema import CanonicalEdge, CanonicalNode, GraphFragment, RejectedEdge


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
        # Prefer the higher-confidence edge as the provenance primary.
        primary = existing if existing.confidence >= edge.confidence else edge
        secondary = edge if primary is existing else existing
        merged_evidence = sorted(
            set(existing.evidence_market_ids) | set(edge.evidence_market_ids)
        )
        merged[key] = primary.model_copy(
            update={
                "confidence": max(existing.confidence, edge.confidence),
                "evidence_market_ids": merged_evidence,
                "evidence_text": primary.evidence_text or secondary.evidence_text,
            }
        )
    return list(merged.values())


def _has_typed_cycle(edges: list[CanonicalEdge], edge_types: frozenset) -> bool:
    if not edges:
        return False

    graph = rx.PyDiGraph()
    node_index: dict[str, int] = {}

    def idx(node_id: str) -> int:
        if node_id not in node_index:
            node_index[node_id] = graph.add_node(node_id)
        return node_index[node_id]

    for edge in edges:
        if edge.edge_type not in edge_types:
            continue
        graph.add_edge(idx(edge.source_id), idx(edge.target_id), edge)

    if graph.num_nodes() == 0:
        return False

    return not rx.is_directed_acyclic_graph(graph)


def _has_progression_cycle(edges: list[CanonicalEdge]) -> bool:
    return _has_typed_cycle(edges, PROGRESSION_EDGE_TYPES)


def _has_implies_cycle(edges: list[CanonicalEdge]) -> bool:
    return _has_typed_cycle(edges, frozenset({EdgeType.IMPLIES}))


def reject_implies_cycle(
    edges: list[CanonicalEdge],
) -> tuple[list[CanonicalEdge], list[RejectedEdge]]:
    """Drop all IMPLIES edges when any IMPLIES cycle is present.

    Non-IMPLIES edges are preserved. When the IMPLIES subgraph is acyclic,
    returns ``edges`` unchanged with an empty rejection list.
    """
    implies = [e for e in edges if e.edge_type == EdgeType.IMPLIES]
    if not implies or not _has_implies_cycle(implies):
        return edges, []
    kept = [e for e in edges if e.edge_type != EdgeType.IMPLIES]
    rejected = [
        RejectedEdge(**edge.model_dump(), rejection_reason="implies_cycle")
        for edge in implies
    ]
    return kept, rejected


# Public aliases used by the build pipeline.
dedupe_edges = _dedupe_edges
has_implies_cycle = _has_implies_cycle


def accept_edges(
    edges: list[CanonicalEdge],
    node_types: dict[str, object],
    settings: Settings,
) -> tuple[list[CanonicalEdge], list[RejectedEdge]]:
    """Apply confidence, evidence, and ontology checks to candidate edges."""
    accepted: list[CanonicalEdge] = []
    rejected: list[RejectedEdge] = []
    for edge in edges:
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
        if not is_allowed_edge(edge.edge_type, source_type, target_type):  # type: ignore[arg-type]
            rejected.append(
                RejectedEdge(**edge.model_dump(), rejection_reason="invalid_pattern")
            )
            continue
        accepted.append(edge)
    return accepted, rejected


def _reject_cyclic_progression_edges(
    edges: list[CanonicalEdge],
) -> tuple[list[CanonicalEdge], list[RejectedEdge]]:
    """Keep non-cyclic progression edges; reject only edges found on cycles."""
    remaining = list(edges)
    rejected: list[RejectedEdge] = []

    while True:
        graph = rx.PyDiGraph()
        node_index: dict[str, int] = {}
        # Map directed node-index pairs to remaining-edge indexes (parallel edges rare).
        pair_to_indexes: dict[tuple[int, int], list[int]] = {}

        def idx(node_id: str) -> int:
            if node_id not in node_index:
                node_index[node_id] = graph.add_node(node_id)
            return node_index[node_id]

        for i, edge in enumerate(remaining):
            src = idx(edge.source_id)
            tgt = idx(edge.target_id)
            graph.add_edge(src, tgt, i)
            pair_to_indexes.setdefault((src, tgt), []).append(i)

        if graph.num_nodes() == 0 or rx.is_directed_acyclic_graph(graph):
            break

        cycle_pairs = list(rx.digraph_find_cycle(graph))
        if not cycle_pairs:
            # Safety: if the DAG check failed but no cycle was reported, reject all.
            for edge in remaining:
                rejected.append(
                    RejectedEdge(**edge.model_dump(), rejection_reason="progression_cycle")
                )
            remaining = []
            break

        drop: set[int] = set()
        for src, tgt in cycle_pairs:
            indexes = pair_to_indexes.get((src, tgt), [])
            if indexes:
                drop.add(indexes[0])
        if not drop:
            for edge in remaining:
                rejected.append(
                    RejectedEdge(**edge.model_dump(), rejection_reason="progression_cycle")
                )
            remaining = []
            break

        next_remaining: list[CanonicalEdge] = []
        for i, edge in enumerate(remaining):
            if i in drop:
                rejected.append(
                    RejectedEdge(**edge.model_dump(), rejection_reason="progression_cycle")
                )
            else:
                next_remaining.append(edge)
        remaining = next_remaining

    return remaining, rejected


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
        derivation = (
            "compiler" if method == "proposition_compiler" else "extraction"
        )
        for edge in fragment.edges:
            source_id = resolution_state.local_to_canonical.get(edge.source)
            target_id = resolution_state.local_to_canonical.get(edge.target)
            # Keep unresolved / missing endpoints so accept_edges can reject them
            # with ``missing_endpoint`` instead of silently dropping the edge.
            raw_edges.append(
                CanonicalEdge(
                    source_id=source_id or edge.source,
                    target_id=target_id or edge.target,
                    edge_type=edge.type,
                    confidence=edge.confidence,
                    evidence_market_ids=list(edge.evidence_market_ids),
                    evidence_text=edge.evidence_text,
                    inference_method=method,
                    derivation_type=derivation,
                )
            )

    deduped = _dedupe_edges(raw_edges)
    accepted, rejected = accept_edges(deduped, node_types, settings)

    progression_edges = [e for e in accepted if e.edge_type in PROGRESSION_EDGE_TYPES]
    non_progression = [e for e in accepted if e.edge_type not in PROGRESSION_EDGE_TYPES]
    kept_progression, cycle_rejected = _reject_cyclic_progression_edges(progression_edges)
    accepted = non_progression + kept_progression
    rejected.extend(cycle_rejected)

    accepted, implies_rejected = reject_implies_cycle(accepted)
    rejected.extend(implies_rejected)

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

    implies = [e for e in edges if e.edge_type == EdgeType.IMPLIES]
    if _has_implies_cycle(implies):
        errors.append("implies cycle detected")

    return errors
