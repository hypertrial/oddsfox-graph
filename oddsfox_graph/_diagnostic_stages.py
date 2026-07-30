from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .artifacts import artifact_projection
from .contracts import DERIVED_EDGE_COLUMNS, validate_relation_columns
from .queries import DuckDB, q
from .sql import create_table_from_rows_sql


DERIVED_EDGE_EMPTY_TYPES = {
    "src_node_id": "VARCHAR",
    "dst_node_id": "VARCHAR",
    "edge_type": "VARCHAR",
    "edge_basis": "VARCHAR",
    "confidence": "DOUBLE",
    "path": "VARCHAR",
    "evidence": "VARCHAR",
}


def compute_transitive_closure(db: DuckDB) -> None:
    """Build internal derived implication edges for conditionals; do not publish."""
    edges = db.rows(
        """
        SELECT src_node_id, dst_node_id, confidence, evidence
        FROM logic_edges_v
        WHERE edge_type = 'implies'
        """
    )
    graph: dict[str, set[str]] = defaultdict(set)
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for row in edges:
        src = str(row["src_node_id"])
        dst = str(row["dst_node_id"])
        graph[src].add(dst)
        meta[(src, dst)] = row

    derived: list[dict[str, Any]] = []
    for start in graph:
        visited: set[str] = set()
        queue: deque[tuple[str, list[str]]] = deque((n, [start, n]) for n in graph[start])
        while queue:
            node, path = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for nxt in graph.get(node, ()):
                new_path = path + [nxt]
                if len(new_path) > 2:
                    src, dst = new_path[0], new_path[-1]
                    if (src, dst) not in meta:
                        base = meta.get((new_path[0], new_path[1]), {})
                        derived.append(
                            {
                                "src_node_id": src,
                                "dst_node_id": dst,
                                "edge_type": "implies",
                                "edge_basis": "transitive",
                                "confidence": float(base.get("confidence") or 1.0),
                                "path": "->".join(new_path),
                                "evidence": "transitive closure of accepted implications",
                            }
                        )
                        meta[(src, dst)] = derived[-1]
                queue.append((nxt, new_path))

    db.execute(
        create_table_from_rows_sql(
            "derived_edges_v",
            derived,
            DERIVED_EDGE_COLUMNS,
            DERIVED_EDGE_EMPTY_TYPES,
        )
    )
    validate_relation_columns(db, "derived_edges_v")


def write_conditionals(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        """
        CREATE TABLE conditional_edges_v AS
        WITH all_logic AS (
            SELECT src_node_id, dst_node_id, edge_type, confidence, evidence
            FROM logic_edges_v
            UNION ALL
            SELECT src_node_id, dst_node_id, edge_type, confidence, evidence
            FROM derived_edges_v
        ),
        exact_exclusion AS (
            SELECT
                src_node_id AS a_node_id,
                dst_node_id AS b_node_id,
                0.0 AS p_a_given_b,
                CASE
                    WHEN edge_type = 'complement' THEN 'exact_complement'
                    ELSE 'exact_exclusion'
                END AS method,
                confidence,
                evidence
            FROM all_logic
            WHERE edge_type IN ('complement', 'mutually_exclusive')
            UNION ALL
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                0.0 AS p_a_given_b,
                CASE
                    WHEN edge_type = 'complement' THEN 'exact_complement'
                    ELSE 'exact_exclusion'
                END AS method,
                confidence,
                evidence
            FROM all_logic
            WHERE edge_type IN ('complement', 'mutually_exclusive')
        ),
        exact_equivalence AS (
            SELECT
                src_node_id AS a_node_id,
                dst_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_equivalence' AS method,
                confidence,
                evidence
            FROM all_logic
            WHERE edge_type = 'equivalent'
            UNION ALL
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_equivalence' AS method,
                confidence,
                evidence
            FROM all_logic
            WHERE edge_type = 'equivalent'
        ),
        exact_implication AS (
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_implication' AS method,
                confidence,
                evidence
            FROM all_logic
            WHERE edge_type = 'implies'
        )
        SELECT * FROM exact_exclusion
        UNION ALL SELECT * FROM exact_equivalence
        UNION ALL SELECT * FROM exact_implication;
        """
    )
    validate_relation_columns(db, "conditional_edges_v")
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("conditional_edges.parquet")}
            FROM conditional_edges_v
        ) TO '{q(out_dir / "conditional_edges.parquet")}' (FORMAT PARQUET);
        """
    )
