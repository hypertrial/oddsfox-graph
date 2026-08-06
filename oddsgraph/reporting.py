"""Inference report generation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from oddsgraph.graphbuild import GraphBuildResult
from oddsgraph.resolution import ResolutionState
from oddsgraph.schema import InferenceReport


def load_inference_report(path: Path) -> InferenceReport:
    if not path.exists():
        return InferenceReport()
    try:
        return InferenceReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValidationError, ValueError):
        return InferenceReport()


def merge_per_event_status(path: Path, updates: dict[str, str]) -> InferenceReport:
    report = load_inference_report(path)
    merged_status = {**report.per_event_status, **updates}
    report = report.model_copy(update={"per_event_status": merged_status})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def build_inference_report(
    resolution_state: ResolutionState,
    graph_result: GraphBuildResult,
    model_path: str | None = None,
    per_event_status: dict[str, str] | None = None,
) -> InferenceReport:
    node_counts = Counter(n.type.value for n in graph_result.nodes)
    edge_counts = Counter(e.edge_type.value for e in graph_result.edges)
    rejected_reasons = Counter(e.rejection_reason for e in graph_result.rejected_edges)

    status = per_event_status or {}
    events_processed = sum(1 for s in status.values() if s == "success")
    events_failed = sum(1 for s in status.values() if s == "failed")
    events_skipped = sum(1 for s in status.values() if s == "skipped")
    events_deterministic = sum(1 for s in status.values() if s == "deterministic")
    events_deterministic_verified = sum(
        1 for s in status.values() if s == "deterministic_verified"
    )
    events_deterministic_corrected = sum(
        1 for s in status.values() if s == "deterministic_corrected"
    )

    return InferenceReport(
        model_path=model_path,
        events_processed=events_processed,
        events_failed=events_failed,
        events_skipped=events_skipped,
        events_deterministic=events_deterministic,
        events_deterministic_verified=events_deterministic_verified,
        events_deterministic_corrected=events_deterministic_corrected,
        node_counts=dict(node_counts),
        edge_counts=dict(edge_counts),
        resolution_tiers=dict(resolution_state.tier_counts),
        rejected_edge_reasons=dict(rejected_reasons),
        per_event_status=status,
    )
