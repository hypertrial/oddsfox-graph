from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from ._discovery.provenance import atomic_write_json
from .queries import DuckDB

GRAPH_SNAPSHOT_ARTIFACT = "graph_snapshot.json"


def write_graph_snapshot(
    db: DuckDB,
    out_dir: Path,
    source_manifest: str = "build_manifest.json",
) -> dict[str, Any]:
    nodes = db.rows(
        """
        SELECT
            node_id,
            market_id,
            question,
            outcome_label,
            canonical_proposition,
            stage_subject AS team,
            stage_key
        FROM nodes_v
        ORDER BY stage_subject NULLS LAST, market_id, outcome_index
        """
    )
    logic_edges = db.rows(
        """
        SELECT
            src_node_id AS source,
            dst_node_id AS target,
            edge_type AS type,
            edge_basis AS basis,
            confidence
        FROM logic_edges_v
        ORDER BY confidence DESC, source, target
        """
    )
    conditionals = db.rows(
        """
        SELECT
            a_node_id,
            b_node_id,
            p_a_given_b,
            method,
            confidence
        FROM conditional_edges_v
        ORDER BY confidence DESC, a_node_id, b_node_id
        """
    )
    source_watermark = db.scalar(
        """
        SELECT max(coalesce(last_seen_ts, first_seen_ts))
        FROM nodes_table
        """
    )
    built_at = (
        source_watermark.isoformat()
        if isinstance(source_watermark, datetime)
        else "1970-01-01T00:00:00+00:00"
    )
    snapshot = {
        "version": f"v{__version__}",
        "built_at": built_at,
        "source_manifest": source_manifest,
        "counts": {
            "nodes": len(nodes),
            "logic_edges": len(logic_edges),
            "conditionals": len(conditionals),
        },
        "nodes": nodes,
        "logic_edges": logic_edges,
        "conditionals": conditionals,
    }
    atomic_write_json(out_dir / GRAPH_SNAPSHOT_ARTIFACT, snapshot)
    return snapshot
