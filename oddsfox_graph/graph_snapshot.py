from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
            stage_key,
            current_price,
            current_price_devig
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
            confidence,
            current_p_src,
            current_p_dst
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
            lower_bound,
            upper_bound,
            method,
            confidence
        FROM conditional_edges_v
        WHERE p_a_given_b IS NULL OR p_a_given_b BETWEEN 0 AND 1
        ORDER BY confidence DESC, a_node_id, b_node_id
        """
    )
    violations = db.rows(
        """
        SELECT
            violation_id AS id,
            violation_type AS type,
            severity,
            description,
            src_node_id,
            dst_node_id,
            market_id_src,
            market_id_dst
        FROM violations_v
        ORDER BY severity, id
        """
    )
    snapshot = {
        "version": "v0.1.0",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": source_manifest,
        "counts": {
            "nodes": len(nodes),
            "logic_edges": len(logic_edges),
            "conditionals": len(conditionals),
            "violations": len(violations),
        },
        "nodes": nodes,
        "logic_edges": logic_edges,
        "conditionals": conditionals,
        "violations": violations,
    }
    (out_dir / GRAPH_SNAPSHOT_ARTIFACT).write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return snapshot
