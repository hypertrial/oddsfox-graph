from __future__ import annotations

import json
from pathlib import Path

from oddsfox_graph.artifacts import ARTIFACT_COLUMNS, PARQUET_ARTIFACTS
from oddsfox_graph.build import build
from oddsfox_graph.graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT
from oddsfox_graph.queries import DuckDB, q
from tests.synthetic import write_hourly_synthetic_input, write_synthetic_input


def test_hourly_full_build_preserves_artifact_schemas_and_manifest(tmp_path: Path) -> None:
    input_path = tmp_path / "hourly.parquet"
    out = tmp_path / "out"
    write_hourly_synthetic_input(input_path)

    build(input_path, out)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "hourly"
    assert manifest["input_granularity_seconds"] == 3600
    assert set(manifest["artifacts"]) == set(PARQUET_ARTIFACTS) | {GRAPH_SNAPSHOT_ARTIFACT}

    db = DuckDB()
    try:
        for artifact in PARQUET_ARTIFACTS:
            rows = db.rows(f"DESCRIBE SELECT * FROM read_parquet('{q(out / artifact)}')")
            assert [row["column_name"] for row in rows] == ARTIFACT_COLUMNS[artifact]

        implies = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE edge_type = 'implies'
                    AND edge_basis = 'stage_progression_rule'
                """
            )
            or 0
        )
        assert implies >= 1
    finally:
        db.close()


def test_minutely_manifest_records_legacy_granularity(tmp_path: Path) -> None:
    input_path = tmp_path / "minutely.parquet"
    out = tmp_path / "out"
    write_synthetic_input(input_path)

    build(input_path, out)

    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_format"] == "minutely"
    assert manifest["input_granularity_seconds"] == 60
