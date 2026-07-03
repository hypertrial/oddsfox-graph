from __future__ import annotations

import json
from pathlib import Path

from oddsgraph.artifacts import ARTIFACT_COLUMNS, PARQUET_ARTIFACTS
from oddsgraph.build import build
from oddsgraph.queries import DuckDB, q
from oddsgraph.thresholds import bucket_counts
from tests.synthetic import write_hourly_synthetic_input, write_synthetic_input


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

    build(input_path, out)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "hourly"
    assert manifest["input_granularity_seconds"] == 3600
    assert manifest["threshold_bucket_counts"]["active_buckets"] == 17
    assert manifest["threshold_bucket_counts"]["overlap_buckets"] == 17
    assert set(manifest["artifacts"]) == set(PARQUET_ARTIFACTS)

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

    build(input_path, out, write_prices=False, solve_coherence=False)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "minutely"
    assert manifest["input_granularity_seconds"] == 60
    assert manifest["threshold_bucket_counts"]["active_buckets"] == 1000
    assert manifest["threshold_bucket_counts"]["overlap_buckets"] == 1000


def test_hourly_price_only_edges_use_scaled_overlap_threshold(tmp_path: Path) -> None:
    input_path = tmp_path / "hourly.parquet"
    out = tmp_path / "out"
    write_hourly_synthetic_input(input_path)

    build(input_path, out, solve_coherence=False)

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

    build(input_path, out, solve_coherence=False)

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
