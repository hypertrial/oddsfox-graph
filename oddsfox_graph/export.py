"""Export graph artifacts to parquet and JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsfox_graph.ontology import dump_ontology_json
from oddsfox_graph.schema import CanonicalEdge, CanonicalNode, InferenceReport, RejectedEdge, UnresolvedEntity


def _write_parquet(path: Path, rows: list[dict]) -> None:
    if not rows:
        pq.write_table(pa.table({}), path)
        return
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def export_graph_artifacts(
    nodes: list[CanonicalNode],
    edges: list[CanonicalEdge],
    rejected_edges: list[RejectedEdge],
    unresolved: list[UnresolvedEntity],
    report: InferenceReport,
    nodes_path: Path,
    edges_path: Path,
    rejected_edges_path: Path,
    unresolved_entities_path: Path,
    ontology_path: Path,
    inference_report_path: Path,
) -> None:
    _write_parquet(nodes_path, [n.model_dump() for n in nodes])
    _write_parquet(
        edges_path,
        [
            {
                "source_id": e.source_id,
                "target_id": e.target_id,
                "edge_type": e.edge_type.value,
                "confidence": e.confidence,
                "evidence_market_ids": e.evidence_market_ids,
                "evidence_text": e.evidence_text,
                "inference_method": e.inference_method,
            }
            for e in edges
        ],
    )
    _write_parquet(
        rejected_edges_path,
        [
            {
                "source_id": e.source_id,
                "target_id": e.target_id,
                "edge_type": e.edge_type.value,
                "confidence": e.confidence,
                "evidence_market_ids": e.evidence_market_ids,
                "evidence_text": e.evidence_text,
                "inference_method": e.inference_method,
                "rejection_reason": e.rejection_reason,
            }
            for e in rejected_edges
        ],
    )
    _write_parquet(unresolved_entities_path, [u.model_dump() for u in unresolved])
    ontology_path.write_text(json.dumps(dump_ontology_json(), indent=2), encoding="utf-8")
    inference_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
