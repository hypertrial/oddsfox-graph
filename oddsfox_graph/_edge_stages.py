from __future__ import annotations

from pathlib import Path

from .artifacts import artifact_projection
from .contracts import validate_relation_columns
from .queries import DuckDB, q


def write_candidates(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE candidate_edges_v AS
        WITH same_market_binary AS (
            SELECT
                a.node_id AS src_node_id,
                b.node_id AS dst_node_id,
                'complement' AS candidate_type,
                'same_market' AS candidate_source,
                1.0 AS candidate_score,
                a.market_id AS market_id_src,
                b.market_id AS market_id_dst,
                a.event_slug AS event_slug_src,
                b.event_slug AS event_slug_dst
            FROM nodes_v a
            JOIN nodes_v b
                ON a.market_id = b.market_id
                AND a.outcome_index < b.outcome_index
            WHERE a.expected_tokens = 2
        ),
        same_market_nary AS (
            SELECT
                a.node_id AS src_node_id,
                b.node_id AS dst_node_id,
                'mutual_exclusion' AS candidate_type,
                'same_market' AS candidate_source,
                1.0 AS candidate_score,
                a.market_id AS market_id_src,
                b.market_id AS market_id_dst,
                a.event_slug AS event_slug_src,
                b.event_slug AS event_slug_dst
            FROM nodes_v a
            JOIN nodes_v b
                ON a.market_id = b.market_id
                AND a.outcome_index < b.outcome_index
            WHERE a.expected_tokens > 2
        ),
        exact_duplicates AS (
            SELECT
                a.node_id AS src_node_id,
                b.node_id AS dst_node_id,
                'equivalence' AS candidate_type,
                'exact_duplicate_same_event' AS candidate_source,
                1.0 AS candidate_score,
                a.market_id AS market_id_src,
                b.market_id AS market_id_dst,
                a.event_slug AS event_slug_src,
                b.event_slug AS event_slug_dst
            FROM nodes_v a
            JOIN nodes_v b
                ON a.event_slug = b.event_slug
                AND a.canonical_proposition = b.canonical_proposition
                AND a.market_id < b.market_id
        ),
        single_winner AS (
            SELECT
                a.node_id AS src_node_id,
                b.node_id AS dst_node_id,
                'mutual_exclusion' AS candidate_type,
                'semantic_single_winner' AS candidate_source,
                1.0 AS candidate_score,
                a.market_id AS market_id_src,
                b.market_id AS market_id_dst,
                a.event_slug AS event_slug_src,
                b.event_slug AS event_slug_dst
            FROM nodes_v a
            JOIN nodes_v b
                ON a.event_slug = b.event_slug
                AND a.market_id < b.market_id
            WHERE a.is_progression_node
                AND b.is_progression_node
                AND a.canonical_proposition != b.canonical_proposition
                AND a.is_single_winner_family
                AND b.is_single_winner_family
        ),
        stage_progression AS (
            SELECT
                a.node_id AS src_node_id,
                b.node_id AS dst_node_id,
                'implication' AS candidate_type,
                'semantic_stage_progression' AS candidate_source,
                1.0 AS candidate_score,
                a.market_id AS market_id_src,
                b.market_id AS market_id_dst,
                a.event_slug AS event_slug_src,
                b.event_slug AS event_slug_dst
            FROM nodes_v a
            JOIN nodes_v b
                ON a.stage_subject = b.stage_subject
                AND a.stage_rank > b.stage_rank
                AND a.market_id != b.market_id
            WHERE a.is_progression_node
                AND b.is_progression_node
                AND a.stage_subject IS NOT NULL
                AND b.stage_subject IS NOT NULL
        )
        SELECT
            src_node_id,
            dst_node_id,
            candidate_type,
            arg_max(candidate_source, candidate_score) AS candidate_source,
            max(candidate_score) AS candidate_score,
            any_value(market_id_src) AS market_id_src,
            any_value(market_id_dst) AS market_id_dst,
            any_value(event_slug_src) AS event_slug_src,
            any_value(event_slug_dst) AS event_slug_dst
        FROM (
            SELECT * FROM same_market_binary
            UNION ALL SELECT * FROM same_market_nary
            UNION ALL SELECT * FROM exact_duplicates
            UNION ALL SELECT * FROM single_winner
            UNION ALL SELECT * FROM stage_progression
        )
        GROUP BY 1, 2, 3;
        """
    )
    validate_relation_columns(db, "candidate_edges_v")


def accept_logic_edges(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        """
        CREATE TABLE logic_edges_v AS
        SELECT
            c.src_node_id,
            c.dst_node_id,
            CASE
                WHEN c.candidate_type = 'complement' THEN 'complement'
                WHEN c.candidate_type = 'equivalence' THEN 'equivalent'
                WHEN c.candidate_type = 'implication' THEN 'implies'
                WHEN c.candidate_type = 'mutual_exclusion' THEN 'mutually_exclusive'
                ELSE 'related'
            END AS edge_type,
            CASE
                WHEN c.candidate_source = 'same_market' THEN 'same_market'
                WHEN c.candidate_source = 'exact_duplicate_same_event' THEN 'exact_duplicate'
                WHEN c.candidate_source = 'semantic_single_winner' THEN 'single_winner_family'
                WHEN c.candidate_source = 'semantic_stage_progression' THEN 'stage_progression_rule'
                ELSE 'unknown'
            END AS edge_basis,
            1.0 AS confidence,
            c.market_id_src,
            c.market_id_dst,
            c.event_slug_src,
            c.event_slug_dst,
            CASE
                WHEN c.candidate_type = 'complement' THEN 'same market tokens sum to 1'
                WHEN c.candidate_source = 'exact_duplicate_same_event' THEN 'same canonical proposition in the same event'
                WHEN c.candidate_source = 'semantic_single_winner' THEN 'single-winner family alternatives cannot both occur'
                WHEN c.candidate_source = 'semantic_stage_progression' THEN 'higher tournament stage implies lower stage'
                WHEN c.candidate_source = 'same_market' THEN 'same market n-ary outcomes are mutually exclusive'
                ELSE 'structural candidate'
            END AS evidence
        FROM candidate_edges_v c
        WHERE c.candidate_source IN (
            'same_market',
            'exact_duplicate_same_event',
            'semantic_single_winner',
            'semantic_stage_progression'
        );
        """
    )
    validate_relation_columns(db, "logic_edges_v")
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("logic_edges.parquet")}
            FROM logic_edges_v
        ) TO '{q(out_dir / "logic_edges.parquet")}' (FORMAT PARQUET);
        """
    )
