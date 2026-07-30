from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, TypeVar

from .artifacts import (
    ARTIFACT_COLUMNS,
    FINAL_EDGE_ARTIFACT_TABLES,
    PARQUET_ARTIFACTS,
    REPORTS,
    artifact_projection,
    reports,
)
from ._diagnostic_stages import write_conditionals
from ._edge_stages import accept_logic_edges, write_candidates
from .contracts import INPUT_PRICE_COLUMNS, validate_relation_columns
from .graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT, write_graph_snapshot
from .queries import DuckDB, q
from .reports import write_reports
from .rules import (
    Taxonomy,
    load_taxonomy,
    single_winner_pattern_sql,
    single_winner_values_sql,
    stage_rules_values_sql,
    stage_subject_alias_values_sql,
)
from .schema import InputFormat, create_input_prices, validate_input_schema, validate_input_table


T_ = TypeVar("T_")


def _stage(
    name: str,
    fn: Callable[[], T_],
    timings: dict[str, float] | None = None,
) -> T_:
    t0 = time.time()
    print(f"[oddsfox-graph] {name} ...", file=sys.stderr, flush=True)
    result = fn()
    elapsed = time.time() - t0
    if timings is not None:
        timings[name.strip()] = round(elapsed, 3)
    print(f"[oddsfox-graph] {name} done in {elapsed:.1f}s", file=sys.stderr, flush=True)
    return result


def build(
    input_path: Path,
    out_dir: Path,
    *,
    taxonomy_path: Path | None = None,
) -> dict[str, str | int | float | None]:
    start = time.time()
    taxonomy = load_taxonomy(taxonomy_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_generated(out_dir)
    db_path = out_dir / "oddsfox_graph.duckdb"
    db = DuckDB(db_path)
    stage_timings: dict[str, float] = {}

    def stage(name: str, fn: Callable[[], T_]) -> T_:
        return _stage(name, fn, stage_timings)

    try:
        input_format = stage("validate_input_schema", lambda: validate_input_schema(db, input_path))
        stage("create_input_prices", lambda: _create_input_prices(db, input_path, input_format))
        stage("validate_input", lambda: validate_input_table(db))
        stage("create_identity_tables", lambda: _create_identity_tables(db, taxonomy, stage))
        stage("write_nodes", lambda: _write_nodes(db, out_dir))
        stage("write_market_groups", lambda: _write_market_groups(db, out_dir))
        stage("write_candidates", lambda: write_candidates(db))
        stage("accept_logic_edges", lambda: accept_logic_edges(db, out_dir))
        stage("validate_final_edges", lambda: _validate_final_edge_invariants(db))
        stage("write_conditionals", lambda: write_conditionals(db, out_dir))
        stage("write_graph_snapshot", lambda: write_graph_snapshot(db, out_dir))
        stats = stage("stats", lambda: _stats(db, start))
        stage("write_reports", lambda: write_reports(db, out_dir, stats))
        stage("validate_generated_artifacts", lambda: _validate_generated_artifacts(db, out_dir))
        _write_manifest(
            input_path,
            out_dir,
            stats,
            taxonomy=taxonomy,
            input_format=input_format,
            stage_timings=stage_timings,
        )
        return stats
    finally:
        db.close()


# Legacy names from pre-v0.2.0 builds. Cleared so rebuilds into an old directory
# cannot leave stale price/probability artifacts beside the new structural set.
_LEGACY_GENERATED_ARTIFACTS = (
    "prices.parquet",
    "candidate_edges.parquet",
    "price_edges.parquet",
    "derived_edges.parquet",
    "constraint_hyperedges.parquet",
    "violations.parquet",
    "calibration.parquet",
    "coherence.parquet",
    "coherence_repairs.parquet",
    "evaluation.parquet",
    "knockout_artifacts.json",
)

_LEGACY_REPORTS = (
    "top_complement_violations.md",
    "duplicate_candidates.md",
    "price_only_edges.md",
    "evaluation.md",
)


def _clear_generated(out_dir: Path) -> None:
    for name in (
        *PARQUET_ARTIFACTS,
        *_LEGACY_GENERATED_ARTIFACTS,
        GRAPH_SNAPSHOT_ARTIFACT,
        "build_manifest.json",
        "oddsfox_graph.duckdb",
    ):
        path = out_dir / name
        if path.exists():
            path.unlink()
    reports_dir = out_dir / "reports"
    for name in (*REPORTS, *_LEGACY_REPORTS):
        path = reports_dir / name
        if path.exists():
            path.unlink()


def _write_manifest(
    input_path: Path,
    out_dir: Path,
    stats: dict[str, object],
    *,
    taxonomy: Taxonomy,
    input_format: InputFormat,
    stage_timings: dict[str, float],
) -> None:
    manifest = {
        "input": str(input_path),
        "input_format": input_format.name,
        "input_granularity_seconds": input_format.granularity_seconds,
        "taxonomy": {
            "name": taxonomy.name,
            "path": str(taxonomy.source_path),
            "hash": taxonomy.content_hash,
        },
        "artifacts": [
            *PARQUET_ARTIFACTS,
            GRAPH_SNAPSHOT_ARTIFACT,
        ],
        "reports": list(reports()),
        "stats": stats,
        "stage_timings": stage_timings,
    }
    (out_dir / "build_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _validate_generated_artifacts(db: DuckDB, out_dir: Path) -> None:
    missing = [name for name in PARQUET_ARTIFACTS if not (out_dir / name).exists()]
    if missing:
        raise RuntimeError("Missing generated artifacts: " + ", ".join(missing))

    for artifact in PARQUET_ARTIFACTS:
        expected_columns = ARTIFACT_COLUMNS[artifact]
        path = q(out_dir / artifact)
        actual_columns = [
            str(row["column_name"])
            for row in db.rows(f"DESCRIBE SELECT * FROM read_parquet('{path}')")
        ]
        if actual_columns != expected_columns:
            raise RuntimeError(
                f"{artifact} schema drift: expected {expected_columns}, got {actual_columns}"
            )

    for artifact, table in FINAL_EDGE_ARTIFACT_TABLES.items():
        table_count = int(db.scalar(f"SELECT count(*) FROM {table}") or 0)
        file_count = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(out_dir / artifact)}')") or 0
        )
        if table_count != file_count:
            raise RuntimeError(
                f"{artifact} is stale: table has {table_count} rows, artifact has {file_count}"
            )


def _create_input_prices(db: DuckDB, input_path: Path, input_format: InputFormat) -> None:
    create_input_prices(db, input_path, input_format=input_format)
    validate_relation_columns(db, "input_prices")


def _create_identity_tables(
    db: DuckDB,
    taxonomy: Taxonomy,
    stage: Callable[[str, Callable[[], T_]], T_],
) -> None:
    stage("  token_minute_prices", lambda: _create_token_minute_prices(db))
    stage("  validate_token_minute_prices", lambda: _validate_token_minute_prices(db))
    stage("  semantic_tables", lambda: _create_semantic_tables(db, taxonomy))
    stage("  market_token_counts", lambda: _create_market_token_counts(db))
    stage("  token_stats", lambda: _create_token_stats(db))
    stage("  nodes_view", lambda: _create_nodes_view(db, taxonomy))
    stage("  validate_nodes", lambda: _validate_nodes(db))


def _create_token_minute_prices(db: DuckDB) -> None:
    columns = ", ".join(INPUT_PRICE_COLUMNS)
    db.execute(
        f"""
        CREATE TABLE token_minute_prices AS
        SELECT
            {columns}
        FROM (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY clob_token_id, odds_minute_epoch
                    ORDER BY odds_timestamp_epoch DESC
                ) AS rn
            FROM input_prices
        )
        WHERE rn = 1;
        """
    )
    validate_relation_columns(db, "token_minute_prices")


def _create_semantic_tables(db: DuckDB, taxonomy: Taxonomy) -> None:
    db.execute(
        f"""
        CREATE TABLE semantic_stage_rules AS
        SELECT *
        FROM (VALUES
            {stage_rules_values_sql(taxonomy)}
        ) AS t(rule_pattern, stage_rank);

        CREATE TABLE semantic_single_winner_slugs AS
        SELECT *
        FROM (VALUES
            {single_winner_values_sql(taxonomy)}
        ) AS t(event_slug);

        CREATE TABLE semantic_stage_subject_aliases AS
        SELECT *
        FROM (VALUES
            {stage_subject_alias_values_sql(taxonomy)}
        ) AS t(alias_subject, canonical_subject);
        """
    )


def _create_market_token_counts(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE market_token_counts AS
        SELECT market_id, count(DISTINCT clob_token_id) AS expected_tokens
        FROM input_prices
        GROUP BY market_id;
        """
    )
    validate_relation_columns(db, "market_token_counts")


def _create_token_stats(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE token_stats AS
        WITH latest AS (
            SELECT
                clob_token_id,
                arg_max(is_active, odds_timestamp_epoch) AS is_active,
                arg_max(is_closed, odds_timestamp_epoch) AS is_closed
            FROM token_minute_prices
            GROUP BY clob_token_id
        )
        SELECT
            p.clob_token_id AS node_id,
            any_value(p.market_id) AS market_id,
            any_value(p.outcome_index) AS outcome_index,
            any_value(p.clob_token_id) AS clob_token_id,
            any_value(p.question) AS question,
            any_value(p.outcome_label) AS outcome_label,
            any_value(p.event_slug) AS event_slug,
            any_value(l.is_active) AS is_active,
            any_value(l.is_closed) AS is_closed,
            min(p.odds_timestamp) AS first_seen_ts,
            max(p.odds_timestamp) AS last_seen_ts,
            any_value(p.input_canonical_team_name) AS input_canonical_team_name,
            any_value(p.input_stage_key) AS input_stage_key,
            any_value(p.input_stage_rank) AS input_stage_rank,
            any_value(p.input_market_direction) AS input_market_direction,
            any_value(p.input_progression_outcome_label) AS input_progression_outcome_label,
            any_value(p.input_is_progression_token) AS input_is_progression_token,
            any_value(p.input_opposite_clob_token_id) AS input_opposite_clob_token_id,
            any_value(p.input_market_status) AS input_market_status,
            any_value(p.input_is_still_alive) AS input_is_still_alive
        FROM token_minute_prices p
        JOIN latest l USING (clob_token_id)
        JOIN market_token_counts t ON t.market_id = p.market_id
        GROUP BY p.clob_token_id;
        """
    )
    validate_relation_columns(db, "token_stats")


def _create_nodes_view(db: DuckDB, taxonomy: Taxonomy) -> None:
    db.execute(
        f"""
        CREATE VIEW nodes_v AS
        WITH raw_stage_matches AS (
            SELECT node_id, stage_subject, stage_rank
            FROM (
                SELECT
                    s.node_id,
                    regexp_extract(s.question, r.rule_pattern, 1) AS stage_subject,
                    r.stage_rank,
                    row_number() OVER (PARTITION BY s.node_id ORDER BY r.stage_rank DESC) AS rn
                FROM token_stats s
                JOIN semantic_stage_rules r
                    ON regexp_extract(s.question, r.rule_pattern, 1) != ''
            )
            WHERE rn = 1
        ),
        stage_matches AS (
            SELECT
                m.node_id,
                coalesce(a.canonical_subject, m.stage_subject) AS stage_subject,
                m.stage_rank
            FROM raw_stage_matches m
            LEFT JOIN semantic_stage_subject_aliases a
                ON a.alias_subject = m.stage_subject
        ),
        enriched AS (
            SELECT
                s.*,
                CASE
                    WHEN s.outcome_label = 'Yes' THEN s.question
                    WHEN s.outcome_label = 'No' THEN 'NOT(' || s.question || ')'
                    ELSE s.question || ' :: ' || s.outcome_label
                END AS canonical_proposition,
                CASE
                    WHEN s.outcome_label IN ('Yes', 'No') THEN 'binary'
                    ELSE 'named_outcome'
                END AS proposition_type,
                coalesce(s.input_canonical_team_name, m.stage_subject) AS stage_subject,
                coalesce(s.input_stage_rank, m.stage_rank) AS stage_rank,
                coalesce(
                    s.input_stage_key,
                    'rank_' || coalesce(s.input_stage_rank, m.stage_rank)::VARCHAR
                ) AS stage_key,
                s.input_canonical_team_name AS canonical_team_name,
                s.input_market_direction AS market_direction,
                s.input_progression_outcome_label AS progression_outcome_label,
                coalesce(s.input_is_progression_token, s.outcome_label = 'Yes') AS is_progression_node,
                s.input_opposite_clob_token_id AS opposite_clob_token_id,
                s.input_market_status AS market_status,
                s.input_is_still_alive AS is_still_alive,
                CASE
                    WHEN w.event_slug IS NOT NULL
                        OR {single_winner_pattern_sql(taxonomy, "s.event_slug")}
                    THEN true
                    ELSE false
                END AS is_single_winner_family,
                t.expected_tokens
            FROM token_stats s
            JOIN market_token_counts t USING (market_id)
            LEFT JOIN stage_matches m USING (node_id)
            LEFT JOIN semantic_single_winner_slugs w
                ON w.event_slug = s.event_slug
        )
        SELECT
            e.*,
            CASE
                WHEN e.is_single_winner_family THEN 'single_winner'
                WHEN e.stage_rank IS NOT NULL THEN 'stage_progression'
                ELSE 'unknown'
            END AS market_family
        FROM enriched e;
        """
    )
    validate_relation_columns(db, "nodes_v")


def _validate_token_minute_prices(db: DuckDB) -> None:
    _require_zero(
        db,
        "duplicate token-minute rows",
        """
        SELECT count(*)
        FROM (
            SELECT clob_token_id, odds_minute_epoch
            FROM token_minute_prices
            GROUP BY 1, 2
            HAVING count(*) > 1
        )
        """,
    )


def _validate_nodes(db: DuckDB) -> None:
    _require_zero(
        db,
        "duplicate nodes",
        """
        SELECT count(*)
        FROM (
            SELECT node_id
            FROM nodes_v
            GROUP BY 1
            HAVING count(*) > 1
        )
        """,
    )
    _require_zero(
        db,
        "node/token mismatch",
        """
        WITH input_tokens AS (
            SELECT DISTINCT clob_token_id AS node_id
            FROM input_prices
            WHERE market_id IN (SELECT market_id FROM market_token_counts)
        ),
        nodes AS (
            SELECT node_id FROM nodes_v
        )
        SELECT count(*)
        FROM input_tokens i
        FULL OUTER JOIN nodes n USING (node_id)
        WHERE i.node_id IS NULL OR n.node_id IS NULL
        """,
    )


def _validate_final_edge_invariants(db: DuckDB) -> None:
    failures = [
        (
            "duplicate logic edges",
            _count_invariant(
                db,
                """
                SELECT count(*)
                FROM (
                SELECT src_node_id, dst_node_id, edge_type
                FROM logic_edges_v
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
                )
                """,
            ),
        ),
        (
            "unexpected logic edge basis",
            _count_invariant(
                db,
                """
                SELECT count(*)
                FROM logic_edges_v
                WHERE edge_basis NOT IN (
                    'same_market',
                    'exact_duplicate',
                    'single_winner_family',
                    'stage_progression_rule'
                )
                """,
            ),
        ),
    ]
    failed = [f"{name}: {count}" for name, count in failures if count]
    if failed:
        raise RuntimeError("Final edge invariant failed: " + "; ".join(failed))


def _require_zero(db: DuckDB, name: str, sql: str) -> None:
    count = _count_invariant(db, sql)
    if count:
        raise RuntimeError(f"{name}: {count}")


def _count_invariant(db: DuckDB, sql: str) -> int:
    return int(db.scalar(sql) or 0)


def _write_nodes(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("nodes.parquet")}
            FROM nodes_v
        ) TO '{q(out_dir / "nodes.parquet")}' (FORMAT PARQUET);
        """
    )


def _write_market_groups(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("market_groups.parquet")}
            FROM (
                SELECT
                    market_id,
                    any_value(event_slug) AS event_slug,
                    any_value(question) AS question,
                    any_value(market_family) AS market_family,
                    any_value(expected_tokens) AS num_tokens,
                    list(node_id ORDER BY outcome_index) AS token_ids,
                    list(outcome_label ORDER BY outcome_index) AS outcome_labels,
                    bool_or(is_active) AS is_active,
                    bool_or(is_closed) AS is_closed,
                    min(first_seen_ts) AS first_seen_ts,
                    max(last_seen_ts) AS last_seen_ts
                FROM nodes_v
                GROUP BY market_id
            ) AS market_groups
        ) TO '{q(out_dir / "market_groups.parquet")}' (FORMAT PARQUET);
        """
    )


def _stats(db: DuckDB, start: float) -> dict[str, str | int | float | None]:
    row = db.rows(
        """
        SELECT
            (SELECT count(*) FROM input_prices) AS input_rows,
            (SELECT count(DISTINCT market_id) FROM input_prices) AS markets,
            (SELECT count(DISTINCT clob_token_id) FROM input_prices) AS tokens,
            (SELECT count(*) FROM (SELECT market_id FROM input_prices GROUP BY market_id HAVING bool_or(is_active))) AS active_markets,
            (SELECT count(*) FROM (SELECT market_id FROM input_prices GROUP BY market_id HAVING bool_or(is_closed))) AS closed_markets,
            (SELECT min(odds_timestamp) FROM input_prices) AS time_range_start,
            (SELECT max(odds_timestamp) FROM input_prices) AS time_range_end,
            (SELECT count(*) FROM candidate_edges_v) AS candidate_edges,
            (SELECT count(*) FROM logic_edges_v) AS logic_edges,
            (SELECT count(*) FROM conditional_edges_v) AS conditional_edges
        """
    )[0]
    row["runtime_seconds"] = round(time.time() - start, 3)
    return row
