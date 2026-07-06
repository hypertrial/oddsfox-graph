from __future__ import annotations

import json
from pathlib import Path

from oddsfox_graph.artifacts import ARTIFACT_COLUMNS, PARQUET_ARTIFACTS
from oddsfox_graph.build import build
from oddsfox_graph.cli import main
from oddsfox_graph.graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT
from oddsfox_graph.knockout import KNOCKOUT_ARTIFACT
from oddsfox_graph.queries import DuckDB, q
from oddsfox_graph.thresholds import bucket_counts
from tests.synthetic import (
    write_hourly_synthetic_input,
    write_stale_current_hourly_input,
    write_synthetic_input,
)


def test_threshold_bucket_counts_convert_duration_intent() -> None:
    minutely = bucket_counts(60)
    hourly = bucket_counts(3600)

    assert minutely.active_buckets == 1000
    assert minutely.overlap_buckets == 1000
    assert hourly.active_buckets == 17
    assert hourly.overlap_buckets == 17
    assert hourly.complement_low_overlap_buckets == 1
    assert hourly.violation_persistence_buckets == 1
    assert hourly.persistence_lookback_buckets == 3
    assert hourly.persistence_lookback_seconds == 10_800


def test_hourly_full_build_preserves_artifact_schemas_and_manifest(tmp_path: Path) -> None:
    input_path = tmp_path / "hourly.parquet"
    out = tmp_path / "out"
    write_hourly_synthetic_input(input_path)

    build(input_path, out, current_max_age_hours=None)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "hourly"
    assert manifest["input_granularity_seconds"] == 3600
    assert manifest["threshold_bucket_counts"]["active_buckets"] == 17
    assert manifest["threshold_bucket_counts"]["overlap_buckets"] == 17
    assert set(manifest["artifacts"]) == set(PARQUET_ARTIFACTS) | {
        GRAPH_SNAPSHOT_ARTIFACT,
        KNOCKOUT_ARTIFACT,
    }

    db = DuckDB()
    try:
        for artifact in PARQUET_ARTIFACTS:
            rows = db.rows(f"DESCRIBE SELECT * FROM read_parquet('{q(out / artifact)}')")
            assert [row["column_name"] for row in rows] == ARTIFACT_COLUMNS[artifact]
    finally:
        db.close()


def test_minutely_manifest_records_legacy_granularity(tmp_path: Path) -> None:
    input_path = tmp_path / "minutely.parquet"
    out = tmp_path / "out"
    write_synthetic_input(input_path)

    build(
        input_path,
        out,
        write_prices=False,
        solve_coherence=False,
        current_max_age_hours=None,
    )

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "minutely"
    assert manifest["input_granularity_seconds"] == 60
    assert manifest["threshold_bucket_counts"]["active_buckets"] == 1000
    assert manifest["threshold_bucket_counts"]["overlap_buckets"] == 1000


def test_hourly_price_only_edges_use_scaled_overlap_threshold(tmp_path: Path) -> None:
    input_path = tmp_path / "hourly.parquet"
    out = tmp_path / "out"
    write_hourly_synthetic_input(input_path)

    build(input_path, out, solve_coherence=False, current_max_age_hours=None)

    db = DuckDB()
    try:
        price_edges = db.rows(f"""
            SELECT edge_type, overlap_minutes
            FROM read_parquet('{q(out / "price_edges.parquet")}')
            WHERE event_slug_src = 'hourly-price-event'
            ORDER BY edge_type
        """)
        low_support_price_edges = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "price_edges.parquet")}')
            WHERE event_slug_src = 'hourly-low-support-event'
        """) or 0)
    finally:
        db.close()

    assert price_edges
    assert {row["overlap_minutes"] for row in price_edges} == {24}
    assert low_support_price_edges == 0


def test_stage_subject_aliases_create_progression_edges(tmp_path: Path) -> None:
    input_path = tmp_path / "hourly.parquet"
    out = tmp_path / "out"
    write_hourly_synthetic_input(input_path)

    build(input_path, out, solve_coherence=False, current_max_age_hours=None)

    db = DuckDB()
    try:
        edges = {
            (row["src_node_id"], row["dst_node_id"])
            for row in db.rows(f"""
                SELECT src_node_id, dst_node_id
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE edge_basis = 'stage_progression_rule'
            """)
        }
    finally:
        db.close()

    assert ("bosnia_final:Yes", "bosnia_r16:Yes") in edges
    assert ("congo_semis:Yes", "congo_qf:Yes") in edges
    assert ("curacao_semis:Yes", "curacao_qf:Yes") in edges


def test_dbt_semantics_allow_no_token_progression(tmp_path: Path) -> None:
    input_path = tmp_path / "semantic.parquet"
    out = tmp_path / "out"
    _write_semantic_hourly_input(input_path)

    build(input_path, out, solve_coherence=False, current_max_age_hours=None)

    artifact = json.loads((out / KNOCKOUT_ARTIFACT).read_text(encoding="utf-8"))
    alpha_r16 = [
        row
        for row in artifact["team_stage_markets"]
        if row["team_id"] == "alpha" and row["stage_key"] == "round_of_16"
    ][0]
    assert alpha_r16["asset_id"] == "alpha_r16_elim:No"
    assert alpha_r16["progression_asset_id"] == "alpha_r16_elim:No"
    assert alpha_r16["opposite_asset_id"] == "alpha_r16_elim:Yes"
    assert alpha_r16["no_asset_id"] == "alpha_r16_elim:No"

    db = DuckDB()
    try:
        edges = {
            (row["src_node_id"], row["dst_node_id"])
            for row in db.rows(f"""
                SELECT src_node_id, dst_node_id
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE edge_basis = 'stage_progression_rule'
            """)
        }
    finally:
        db.close()

    assert ("alpha_final:Yes", "alpha_r16_elim:No") in edges


def test_default_build_excludes_closed_and_stale_current_markets(tmp_path: Path) -> None:
    input_path = tmp_path / "stale_current.parquet"
    out = tmp_path / "out"
    write_stale_current_hourly_input(input_path)

    build(input_path, out, write_prices=False, solve_coherence=False)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["build_options"]["current_max_age_hours"] == 48.0
    assert manifest["stats"]["eligible_current_markets"] == 2
    assert manifest["stats"]["current_closed_excluded_markets"] == 2
    assert manifest["stats"]["current_stale_excluded_markets"] == 2

    db = DuckDB()
    try:
        market_ids = {
            row["market_id"]
            for row in db.rows(f"""
                SELECT market_id
                FROM read_parquet('{q(out / "market_groups.parquet")}')
            """)
        }
        stage_edges = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "logic_edges.parquet")}')
            WHERE edge_basis = 'stage_progression_rule'
        """) or 0)
        bad_candidates = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "candidate_edges.parquet")}')
            WHERE market_id_src LIKE 'closed_%'
                OR market_id_src LIKE 'stale_%'
                OR market_id_dst LIKE 'closed_%'
                OR market_id_dst LIKE 'stale_%'
        """) or 0)
        bad_constraints = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "constraint_hyperedges.parquet")}')
            WHERE market_id LIKE 'closed_%' OR market_id LIKE 'stale_%'
        """) or 0)
        bad_violations = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "violations.parquet")}')
            WHERE coalesce(market_id_src, '') LIKE 'closed_%'
                OR coalesce(market_id_src, '') LIKE 'stale_%'
                OR coalesce(market_id_dst, '') LIKE 'closed_%'
                OR coalesce(market_id_dst, '') LIKE 'stale_%'
        """) or 0)
    finally:
        db.close()

    assert market_ids == {"live_r16", "live_qf"}
    assert stage_edges == 1
    assert bad_candidates == 0
    assert bad_constraints == 0
    assert bad_violations == 0


def test_allow_stale_current_preserves_legacy_current_behavior(tmp_path: Path) -> None:
    input_path = tmp_path / "stale_current.parquet"
    out = tmp_path / "out"
    write_stale_current_hourly_input(input_path)

    assert main([
        "build",
        "--input", str(input_path),
        "--out", str(out),
        "--skip-prices",
        "--skip-coherence",
        "--allow-stale-current",
    ]) == 0

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["build_options"]["current_max_age_hours"] is None
    assert manifest["stats"]["eligible_current_markets"] == 6
    assert manifest["stats"]["current_closed_excluded_markets"] == 0
    assert manifest["stats"]["current_stale_excluded_markets"] == 0

    db = DuckDB()
    try:
        market_count = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "market_groups.parquet")}')
        """) or 0)
        stage_edges = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "logic_edges.parquet")}')
            WHERE edge_basis = 'stage_progression_rule'
        """) or 0)
    finally:
        db.close()

    assert market_count == 6
    assert stage_edges == 3


def _write_semantic_hourly_input(path: Path) -> None:
    db = DuckDB(path.with_suffix(".duckdb"))
    try:
        db.execute(f"""
            COPY (
                WITH markets(
                    market_id,
                    question,
                    event_slug,
                    yes_price,
                    no_price,
                    stage_key,
                    stage_rank,
                    progression_label,
                    progression_outcome
                ) AS (
                    VALUES
                        ('alpha_r16_elim', 'Will Alpha be eliminated in the Round of 16 of the World Cup?', 'alpha-r16', 0.35, 0.65, 'round_of_16', 1, 'No', 'not_eliminated_in_round_of_16'),
                        ('alpha_final', 'Will Alpha reach the 2026 FIFA World Cup final?', 'alpha-final', 0.25, 0.75, 'final', 4, 'Yes', 'reach_final')
                ),
                hour AS (
                    SELECT range::BIGINT AS i
                    FROM range(24)
                )
                SELECT
                    market_id,
                    outcome_index,
                    market_id || ':' || outcome_label AS clob_token_id,
                    question,
                    outcome_label,
                    event_slug,
                    true AS is_active,
                    false AS is_closed,
                    20000.0 AS market_volume_usd,
                    to_timestamp(i * 3600) AS odds_hour_utc,
                    (i * 3600)::BIGINT AS odds_hour_epoch,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS open_price,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS high_price,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS low_price,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS close_price,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS avg_price,
                    1::BIGINT AS observed_points,
                    (i * 3600)::BIGINT AS first_timestamp,
                    to_timestamp(i * 3600) AS first_observed_at,
                    (i * 3600)::BIGINT AS last_timestamp,
                    to_timestamp(i * 3600) AS last_observed_at,
                    'Alpha' AS canonical_team_name,
                    stage_key,
                    stage_rank,
                    CASE WHEN market_id = 'alpha_r16_elim' THEN 'elimination' ELSE 'advance' END AS market_direction,
                    progression_outcome AS progression_outcome_label,
                    outcome_label = progression_label AS is_progression_token,
                    market_id || ':' || CASE outcome_label WHEN 'Yes' THEN 'No' ELSE 'Yes' END AS opposite_clob_token_id,
                    'live' AS market_status,
                    true AS is_still_alive
                FROM markets
                JOIN hour ON true
                CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label)
            ) TO '{q(path)}' (FORMAT PARQUET)
        """)
    finally:
        db.close()
