from __future__ import annotations

from pathlib import Path

from .artifacts import artifact_projection
from .contracts import validate_relation_columns
from .queries import DuckDB, q


def write_conditionals(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        """
        CREATE TABLE conditional_edges_v AS
        WITH exact_exclusion AS (
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
            FROM logic_edges_v
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
            FROM logic_edges_v
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
            FROM logic_edges_v
            WHERE edge_type = 'equivalent'
            UNION ALL
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_equivalence' AS method,
                confidence,
                evidence
            FROM logic_edges_v
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
            FROM logic_edges_v
            WHERE edge_type = 'implies'
        )
        SELECT * FROM exact_exclusion
        UNION ALL SELECT * FROM exact_equivalence
        UNION ALL SELECT * FROM exact_implication
        ORDER BY a_node_id, b_node_id, method;
        """
    )
    validate_relation_columns(db, "conditional_edges_v")
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("conditional_edges.parquet")}
            FROM conditional_edges_v
            ORDER BY a_node_id, b_node_id, method
        ) TO '{q(out_dir / "conditional_edges.parquet")}' (FORMAT PARQUET);
        """
    )
