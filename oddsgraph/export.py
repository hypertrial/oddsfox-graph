"""Export graph artifacts to parquet and JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.ontology import dump_ontology_json
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport, RejectedEdge

_STRING_LIST = pa.list_(pa.string())

_NODE_SCHEMA = pa.schema(
    [
        ("canonical_id", pa.string()),
        ("type", pa.string()),
        ("label", pa.string()),
        ("aliases", _STRING_LIST),
        ("confidence", pa.float64()),
        ("evidence_market_ids", _STRING_LIST),
        ("resolution_method", pa.string()),
        ("inference_method", pa.string()),
        ("proposition_json", pa.string()),
    ]
)

_EDGE_SCHEMA = pa.schema(
    [
        ("source_id", pa.string()),
        ("target_id", pa.string()),
        ("edge_type", pa.string()),
        ("confidence", pa.float64()),
        ("evidence_market_ids", _STRING_LIST),
        ("evidence_text", pa.string()),
        ("inference_method", pa.string()),
        ("derivation_type", pa.string()),
        ("rule_id", pa.string()),
        ("rule_version", pa.int64()),
        ("premises", _STRING_LIST),
    ]
)

_REJECTED_EDGE_SCHEMA = pa.schema(
    [*_EDGE_SCHEMA, ("rejection_reason", pa.string())]
)


def _table_with_schema(rows: list[dict], schema: pa.Schema) -> pa.Table:
    if not rows:
        return schema.empty_table()
    return pa.Table.from_pylist(rows, schema=schema)


def _write_parquet(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    pq.write_table(_table_with_schema(rows, schema), path)


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
    _write_parquet(nodes_path, [_node_row(n) for n in nodes], _NODE_SCHEMA)
    _write_parquet(edges_path, [_edge_row(e) for e in edges], _EDGE_SCHEMA)
    _write_parquet(
        rejected_edges_path,
        [{**_edge_row(e), "rejection_reason": e.rejection_reason} for e in rejected_edges],
        _REJECTED_EDGE_SCHEMA,
    )
    ontology_path.write_text(json.dumps(dump_ontology_json(), indent=2), encoding="utf-8")
    inference_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
