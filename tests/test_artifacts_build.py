from __future__ import annotations

import json
from pathlib import Path

from oddsfox_graph.artifacts import ARTIFACT_COLUMNS, PARQUET_ARTIFACTS
from oddsfox_graph.build import build
from oddsfox_graph.graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT
from oddsfox_graph.queries import DuckDB, q


def test_build_outputs_structural_artifacts(synthetic_output: Path) -> None:
    db = DuckDB()
    try:
        assert set(PARQUET_ARTIFACTS) <= {p.name for p in synthetic_output.glob("*.parquet")}
        assert (synthetic_output / GRAPH_SNAPSHOT_ARTIFACT).is_file()
        assert (synthetic_output / "build_manifest.json").is_file()
        assert (synthetic_output / "reports" / "summary.md").read_text()
        coverage = (synthetic_output / "reports" / "coverage.md").read_text()
        assert "# Coverage" in coverage
        assert "## Conditionals" in coverage

        for artifact in PARQUET_ARTIFACTS:
            rows = db.rows(f"DESCRIBE SELECT * FROM read_parquet('{q(synthetic_output / artifact)}')")
            assert [row["column_name"] for row in rows] == ARTIFACT_COLUMNS[artifact]

        nodes = db.rows(
            f"""
            SELECT outcome_label, canonical_proposition
            FROM read_parquet('{q(synthetic_output / "nodes.parquet")}')
            WHERE market_id = 'named'
            ORDER BY outcome_label
            """
        )
        assert nodes == [
            {"outcome_label": "Messi", "canonical_proposition": "Top goalscorer? :: Messi"},
            {"outcome_label": "Ronaldo", "canonical_proposition": "Top goalscorer? :: Ronaldo"},
        ]

        market_groups = db.rows(
            f"""
            SELECT num_tokens, token_ids, outcome_labels
            FROM read_parquet('{q(synthetic_output / "market_groups.parquet")}')
            """
        )
        for row in market_groups:
            assert len(row["token_ids"]) == row["num_tokens"]
            assert len(row["outcome_labels"]) == row["num_tokens"]

        complement = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(synthetic_output / "logic_edges.parquet")}')
                WHERE edge_type = 'complement' AND edge_basis = 'same_market'
                """
            )
            or 0
        )
        assert complement >= 1

        methods = {
            row["method"]
            for row in db.rows(
                f"""
                SELECT DISTINCT method
                FROM read_parquet('{q(synthetic_output / "conditional_edges.parquet")}')
                """
            )
        }
        assert "exact_complement" in methods
        assert "exact_implication" in methods
        assert "bounded_frechet" not in methods
        assert "exact_implication_reverse" not in methods

        snapshot = json.loads((synthetic_output / GRAPH_SNAPSHOT_ARTIFACT).read_text(encoding="utf-8"))
        assert snapshot["version"] == "v0.2.0"
        assert "violations" not in snapshot
        assert "nodes" in snapshot
        assert "logic_edges" in snapshot
        assert "conditionals" in snapshot

        manifest = json.loads((synthetic_output / "build_manifest.json").read_text(encoding="utf-8"))
        assert set(manifest["artifacts"]) == set(PARQUET_ARTIFACTS) | {GRAPH_SNAPSHOT_ARTIFACT}
        assert "stage_timings" in manifest
        assert "build_options" not in manifest
    finally:
        db.close()


def test_rebuild_clears_legacy_artifacts(synthetic_input: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "prices.parquet").write_bytes(b"stale")
    (out / "knockout_artifacts.json").write_text("{}", encoding="utf-8")
    reports = out / "reports"
    reports.mkdir()
    (reports / "price_only_edges.md").write_text("stale", encoding="utf-8")

    build(synthetic_input, out)

    assert not (out / "prices.parquet").exists()
    assert not (out / "knockout_artifacts.json").exists()
    assert not (reports / "price_only_edges.md").exists()
    assert (out / "logic_edges.parquet").exists()
    assert (out / "build_manifest.json").exists()


def test_nary_same_market_exclusions(synthetic_output: Path) -> None:
    db = DuckDB()
    try:
        count = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(synthetic_output / "logic_edges.parquet")}')
                WHERE edge_basis = 'same_market'
                    AND edge_type = 'mutually_exclusive'
                    AND market_id_src = 'golden_boot'
                """
            )
            or 0
        )
        assert count == 3
    finally:
        db.close()
