from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
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
    snapshot = {
        "version": f"v{__version__}",
        "built_at": datetime.now(timezone.utc).isoformat(),
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
    (out_dir / GRAPH_SNAPSHOT_ARTIFACT).write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return snapshot
