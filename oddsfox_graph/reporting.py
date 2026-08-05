"""Inference report generation."""

from __future__ import annotations

from collections import Counter

from oddsfox_graph.graphbuild import GraphBuildResult
from oddsfox_graph.resolution import ResolutionState
from oddsfox_graph.schema import InferenceReport


def build_inference_report(
    resolution_state: ResolutionState,
    graph_result: GraphBuildResult,
    model_path: str | None = None,
    per_event_status: dict[str, str] | None = None,
) -> InferenceReport:
    node_counts = Counter(n.type.value for n in graph_result.nodes)
    edge_counts = Counter(e.edge_type.value for e in graph_result.edges)
    rejected_reasons = Counter(e.rejection_reason for e in graph_result.rejected_edges)

    events_processed = sum(
        1 for s in (per_event_status or {}).values() if s == "success"
    )
    events_failed = sum(1 for s in (per_event_status or {}).values() if s == "failed")
    events_skipped = sum(1 for s in (per_event_status or {}).values() if s == "skipped")

    return InferenceReport(
        model_path=model_path,
        events_processed=events_processed,
        events_failed=events_failed,
        events_skipped=events_skipped,
        node_counts=dict(node_counts),
        edge_counts=dict(edge_counts),
        resolution_tiers=dict(resolution_state.tier_counts),
        rejected_edge_reasons=dict(rejected_reasons),
        unresolved_count=len(resolution_state.unresolved),
        per_event_status=per_event_status or {},
    )
