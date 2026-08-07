"""Export graph artifacts to parquet and JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.ontology import dump_ontology_json
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport, RejectedEdge


def _table_with_schema(rows: list[dict], template_row: dict[str, Any]) -> pa.Table:
    if rows:
        return pa.Table.from_pylist(rows)
    return pa.Table.from_pylist([template_row]).slice(0, 0)


def _write_parquet(path: Path, rows: list[dict], template_row: dict[str, Any]) -> None:
    table = _table_with_schema(rows, template_row)
    pq.write_table(table, path)


_NODE_TEMPLATE: dict[str, Any] = {
    "canonical_id": "",
    "type": "TEAM",
    "label": "",
    "aliases": [],
    "confidence": 0.0,
    "evidence_market_ids": [],
    "resolution_method": "",
    "inference_method": "",
    "proposition_json": None,
}

_EDGE_TEMPLATE: dict[str, Any] = {
    "source_id": "",
    "target_id": "",
    "edge_type": "PART_OF",
    "confidence": 0.0,
    "evidence_market_ids": [],
    "evidence_text": "",
    "inference_method": "",
    "derivation_type": "extraction",
    "rule_id": None,
    "rule_version": None,
    "premises": None,
}

_REJECTED_EDGE_TEMPLATE: dict[str, Any] = {
    **_EDGE_TEMPLATE,
    "rejection_reason": "",
}


def _node_row(node: CanonicalNode) -> dict[str, Any]:
    return {
        "canonical_id": node.canonical_id,
        "type": node.type.value if hasattr(node.type, "value") else node.type,
        "label": node.label,
        "aliases": list(node.aliases),
        "confidence": node.confidence,
        "evidence_market_ids": list(node.evidence_market_ids),
        "resolution_method": node.resolution_method,
        "inference_method": node.inference_method,
        "proposition_json": (
            node.proposition.model_dump_json() if node.proposition is not None else None
        ),
    }


def _edge_row(edge: CanonicalEdge) -> dict[str, Any]:
    return {
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type.value,
        "confidence": edge.confidence,
        "evidence_market_ids": edge.evidence_market_ids,
        "evidence_text": edge.evidence_text,
        "inference_method": edge.inference_method,
        "derivation_type": edge.derivation_type,
        "rule_id": edge.rule_id,
        "rule_version": edge.rule_version,
        "premises": edge.premises,
    }


def export_graph_artifacts(
    nodes: list[CanonicalNode],
    edges: list[CanonicalEdge],
    rejected_edges: list[RejectedEdge],
    report: InferenceReport,
    nodes_path: Path,
    edges_path: Path,
    rejected_edges_path: Path,
    ontology_path: Path,
    inference_report_path: Path,
) -> None:
    _write_parquet(nodes_path, [_node_row(n) for n in nodes], _NODE_TEMPLATE)
    _write_parquet(edges_path, [_edge_row(e) for e in edges], _EDGE_TEMPLATE)
    _write_parquet(
        rejected_edges_path,
        [{**_edge_row(e), "rejection_reason": e.rejection_reason} for e in rejected_edges],
        _REJECTED_EDGE_TEMPLATE,
    )
    ontology_path.write_text(json.dumps(dump_ontology_json(), indent=2), encoding="utf-8")
    inference_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
