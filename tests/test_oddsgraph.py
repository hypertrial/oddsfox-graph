from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from oddsgraph.artifacts import ARTIFACT_COLUMNS, ARTIFACT_EMPTY_TYPES, PARQUET_ARTIFACTS
from oddsgraph.build import (
    _create_token_minute_prices,
    _validate_final_edge_invariants,
    _validate_generated_artifacts,
    _validate_token_minute_prices,
    build,
)
from oddsgraph.calibration import _empirical_confidence, apply_calibration_confidence, default_thresholds
from oddsgraph.cli import main
from oddsgraph.coherence import (
    EventModel,
    LpConstraint,
    _collect_coherence_inputs,
    _collect_constraints,
    _collect_constraints_from_inputs,
    _constraints_satisfied,
    _solve_l1_repair,
)
from oddsgraph.queries import DuckDB, q
from oddsgraph.rules import load_taxonomy
from oddsgraph.schema import validate_input
from oddsgraph.sql import sql_literal
from tests.synthetic import write_synthetic_resolutions


ARTIFACTS = set(PARQUET_ARTIFACTS)

BASE_ROWS = [
    ("m1", 0, "m1:Yes", "Will M1 pass?", "Yes", "event-1", True, False, 1.0, 1, 0.4),
    ("m1", 1, "m1:No", "Will M1 pass?", "No", "event-1", True, False, 1.0, 1, 0.6),
]


def test_schema_rejects_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    db = DuckDB(tmp_path / "bad.duckdb")
    try:
        db.execute(f"COPY (SELECT 'm1' AS market_id) TO '{q(path)}' (FORMAT PARQUET)")
        with pytest.raises(ValueError, match="missing required columns"):
            validate_input(db, path)
    finally:
        db.close()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([("m1", 0, "m1:Yes", None, "Yes", "event-1", True, False, 1.0, 1, 0.4),
          ("m1", 1, "m1:No", "Will M1 pass?", "No", "event-1", True, False, 1.0, 1, 0.6)],
         "null required values: 1 rows"),
        ([("m1", 0, "m1:Yes", "Will M1 pass?", "Yes", "event-1", True, False, 1.0, 1, 1.2),
          ("m1", 1, "m1:No", "Will M1 pass?", "No", "event-1", True, False, 1.0, 1, 0.6)],
         "prices outside \\[0, 1\\]: 1 rows"),
        (BASE_ROWS + [BASE_ROWS[0]], "duplicate token timestamp rows: 1 groups"),
        ([*BASE_ROWS,
          ("m1", 0, "m1:Yes", "Will M1 changed pass?", "Yes", "event-1", True, False, 1.0, 2, 0.4),
          ("m1", 1, "m1:No", "Will M1 pass?", "No", "event-1", True, False, 1.0, 2, 0.6)],
         "unstable token metadata: 1 tokens"),
        ([BASE_ROWS[0]], "markets with fewer than 2 tokens: 1 markets"),
        ([("m1", 0, "m1:Yes", "Will M1 pass?", "Yes", "event-1", True, False, 1.0, 1, 0.4),
          ("m1", 1, "m1:No", "Will M1 pass?", "No", "event-1", True, False, 1.0, 61, 0.6)],
         "markets without complete current minute: 1 markets"),
    ],
)
def test_schema_rejects_invalid_invariants(tmp_path: Path, rows: list[tuple[Any, ...]], message: str) -> None:
    path = tmp_path / "bad.parquet"
    _write_input(path, rows)
    db = DuckDB(tmp_path / "bad.duckdb")
    try:
        with pytest.raises(ValueError, match=message):
            validate_input(db, path)
    finally:
        db.close()


def test_build_outputs_artifacts_and_core_logic(synthetic_output: Path) -> None:
    db = DuckDB()
    try:
        assert ARTIFACTS <= {p.name for p in synthetic_output.glob("*.parquet")}
        assert (synthetic_output / "reports" / "summary.md").read_text()
        coverage = (synthetic_output / "reports" / "coverage.md").read_text()
        assert "## Market Families" in coverage
        assert "## Candidate Sources" in coverage
        assert "## Logic Edges" in coverage
        assert "## Price-Only Edges" in coverage

        nodes = db.rows(f"""
            SELECT outcome_label, canonical_proposition
            FROM read_parquet('{q(synthetic_output / "nodes.parquet")}')
            WHERE market_id = 'named'
            ORDER BY outcome_label
        """)
        assert nodes == [
            {"outcome_label": "Messi", "canonical_proposition": "Top goalscorer? :: Messi"},
            {"outcome_label": "Ronaldo", "canonical_proposition": "Top goalscorer? :: Ronaldo"},
        ]

        nary = db.rows(f"""
            SELECT constraint_type, current_sum_price
            FROM read_parquet('{q(synthetic_output / "constraint_hyperedges.parquet")}')
            WHERE market_id = 'golden_boot'
        """)
        assert nary == [{"constraint_type": "one_of_n", "current_sum_price": pytest.approx(1.0)}]

        coherence = db.rows(f"""
            SELECT solver_status, incoherence_distance
            FROM read_parquet('{q(synthetic_output / "coherence.parquet")}')
            WHERE event_slug = 'world-cup-golden-boot-winner'
        """)
        assert coherence == [{"solver_status": "optimal", "incoherence_distance": pytest.approx(0.0)}]

        false_global_violations = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(synthetic_output / "violations.parquet")}')
            WHERE violation_type = 'global_incoherence'
                AND event_slug_src = 'world-cup-golden-boot-winner'
        """))
        assert false_global_violations == 0

        current_sum = float(db.scalar(f"""
            SELECT current_sum_price
            FROM read_parquet('{q(synthetic_output / "market_groups.parquet")}')
            WHERE market_id = 'stale'
        """))
        assert current_sum == pytest.approx(1.0)

        market_groups = db.rows(f"""
            SELECT num_tokens, token_ids, outcome_labels
            FROM read_parquet('{q(synthetic_output / "market_groups.parquet")}')
        """)
        for row in market_groups:
            token_ids = row["token_ids"]
            outcome_labels = row["outcome_labels"]
            assert len(token_ids) == row["num_tokens"]
            assert len(outcome_labels) == row["num_tokens"]
            assert len(set(token_ids)) == len(token_ids)
            assert len(set(outcome_labels)) == len(outcome_labels)

        duplicate_candidates = int(db.scalar(f"""
            SELECT count(*)
            FROM (
                SELECT src_node_id, dst_node_id, candidate_type
                FROM read_parquet('{q(synthetic_output / 'candidate_edges.parquet')}')
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            )
        """))
        assert duplicate_candidates == 0

        violations = db.rows(f"""
            SELECT violation_type
            FROM read_parquet('{q(synthetic_output / "violations.parquet")}')
            WHERE market_id_src = 'bad'
        """)
        assert violations == [{"violation_type": "complement_violation"}]

        conditionals = db.rows(f"""
            SELECT p_a_given_b
            FROM read_parquet('{q(synthetic_output / "conditional_edges.parquet")}')
            WHERE method = 'exact_implication_reverse'
        """)
        assert all(row["p_a_given_b"] is None or row["p_a_given_b"] <= 1.0 for row in conditionals)

        methods = {
            row["method"]
            for row in db.rows(
                f"SELECT DISTINCT method FROM read_parquet('{q(synthetic_output / 'conditional_edges.parquet')}')"
            )
        }
        assert {
            "exact_complement",
            "exact_implication",
            "exact_implication_reverse",
            "exact_exclusion",
            "bounded_frechet",
        } <= methods
    finally:
        db.close()


def test_artifact_schemas_match_contract(synthetic_output: Path) -> None:
    db = DuckDB()
    try:
        for artifact in PARQUET_ARTIFACTS:
            expected = ARTIFACT_COLUMNS[artifact]
            rows = db.rows(f"DESCRIBE SELECT * FROM read_parquet('{q(synthetic_output / artifact)}')")
            assert [row["column_name"] for row in rows] == expected
    finally:
        db.close()


def test_artifact_empty_type_contracts_match_columns() -> None:
    for artifact, empty_types in ARTIFACT_EMPTY_TYPES.items():
        assert list(empty_types) == ARTIFACT_COLUMNS[artifact]


def test_generated_artifact_validation_reports_missing_files(tmp_path: Path) -> None:
    db = DuckDB()
    try:
        with pytest.raises(RuntimeError, match="Missing generated artifacts"):
            _validate_generated_artifacts(db, tmp_path, has_evaluation=False)
    finally:
        db.close()


def test_generated_artifact_validation_reports_schema_drift(
    synthetic_output: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    for artifact in PARQUET_ARTIFACTS:
        shutil.copy2(synthetic_output / artifact, out / artifact)
    db = DuckDB()
    try:
        db.execute(f"COPY (SELECT 'bad' AS node_id) TO '{q(out / 'nodes.parquet')}' (FORMAT PARQUET)")
        with pytest.raises(RuntimeError, match=r"nodes\.parquet schema drift"):
            _validate_generated_artifacts(db, out, has_evaluation=False)
    finally:
        db.close()


def test_semantic_rule_classification(synthetic_output: Path) -> None:
    db = DuckDB()
    try:
        families = {
            row["market_id"]: row["market_family"]
            for row in db.rows(f"""
                SELECT market_id, market_family
                FROM read_parquet('{q(synthetic_output / "market_groups.parquet")}')
                WHERE market_id IN ('comp', 'winner_alpha', 'alpha_final', 'alpha_semis', 'golden_boot')
            """)
        }
        assert families == {
            "comp": "unknown",
            "winner_alpha": "single_winner",
            "alpha_final": "stage_progression",
            "alpha_semis": "stage_progression",
            "golden_boot": "single_winner",
        }
        sources = {
            row["candidate_source"]
            for row in db.rows(f"""
                SELECT DISTINCT candidate_source
                FROM read_parquet('{q(synthetic_output / "candidate_edges.parquet")}')
            """)
        }
        assert {"exact_duplicate_same_event", "semantic_single_winner", "semantic_stage_progression"} <= sources
    finally:
        db.close()


def test_build_manifest_marks_success(synthetic_output: Path) -> None:
    manifest = json.loads((synthetic_output / "build_manifest.json").read_text())
    assert set(manifest["artifacts"]) == ARTIFACTS
    assert manifest["stats"]["tokens"] > 0
    assert manifest["taxonomy"]["name"] == "wc2026"
    assert manifest["effective_thresholds"] is not None
    assert manifest["build_options"] == {
        "fast_graph": False,
        "graph_lookback_days": 30,
        "solve_coherence": True,
        "write_prices": True,
    }
    assert manifest["stats"]["history_mode"] == "full"
    assert manifest["stage_timings"]["create_input_prices"] >= 0
    assert manifest["stage_timings"]["token_minute_prices"] >= 0
    assert "reports/summary.md" in manifest["reports"]
    assert "reports/coverage.md" in manifest["reports"]
    db = DuckDB()
    try:
        for stat_key, artifact in (
            ("logic_edges", "logic_edges.parquet"),
            ("price_edges", "price_edges.parquet"),
        ):
            artifact_count = int(db.scalar(f"""
                SELECT count(*)
                FROM read_parquet('{q(synthetic_output / artifact)}')
            """))
            assert manifest["stats"][stat_key] == artifact_count
    finally:
        db.close()


def test_market_minute_sums_match_market_group_artifact(synthetic_output: Path) -> None:
    db = DuckDB(synthetic_output / "oddsgraph.duckdb")
    try:
        rows = db.rows(f"""
            WITH market_group_rows AS (
                SELECT market_id, current_sum_price, mean_sum_price
                FROM read_parquet('{q(synthetic_output / "market_groups.parquet")}')
            ),
            sum_rows AS (
                SELECT
                    market_id,
                    max(CASE WHEN is_current_complete THEN scoring_price_sum END) AS current_sum_price,
                    avg(scoring_price_sum) FILTER (WHERE is_complete) AS mean_sum_price
                FROM market_minute_sums
                GROUP BY market_id
            )
            SELECT
                g.market_id,
                g.current_sum_price AS artifact_current_sum_price,
                s.current_sum_price AS table_current_sum_price,
                g.mean_sum_price AS artifact_mean_sum_price,
                s.mean_sum_price AS table_mean_sum_price
            FROM market_group_rows g
            JOIN sum_rows s USING (market_id)
        """)
    finally:
        db.close()

    assert rows
    for row in rows:
        assert row["artifact_current_sum_price"] == pytest.approx(row["table_current_sum_price"])
        assert row["artifact_mean_sum_price"] == pytest.approx(row["table_mean_sum_price"])


def test_taxonomy_loader_round_trip() -> None:
    taxonomy = load_taxonomy()
    assert taxonomy.name == "wc2026"
    assert len(taxonomy.stage_rules) == 5
    assert "world-cup-winner" in taxonomy.single_winner_slugs


def test_lp_constraint_senses_preserve_feasible_observations() -> None:
    model = EventModel(
        "constraint-sense",
        ["a", "b", "c", "d"],
        pytest.importorskip("numpy").array([0.4, 0.6, 0.2, 0.2]),
        {"a": 0, "b": 1, "c": 2, "d": 3},
    )
    constraints = [
        LpConstraint("simplex", "eq", [(0, 1.0), (1, 1.0)], 1.0),
        LpConstraint("complement", "eq", [(0, 1.0), (1, 1.0)], 1.0),
        LpConstraint("equivalent", "eq", [(2, 1.0), (3, -1.0)], 0.0),
        LpConstraint("implies", "le", [(2, 1.0), (0, -1.0)], 0.0),
        LpConstraint("exclusion", "le", [(0, 1.0), (2, 1.0)], 1.0),
        LpConstraint("family_sum", "le", [(2, 1.0), (3, 1.0)], 1.0),
    ]

    repaired, distance, status = _solve_l1_repair(model, constraints)

    assert status == "optimal"
    assert distance == pytest.approx(0.0)
    assert list(repaired) == pytest.approx(list(model.observed))
    assert _constraints_satisfied(model, constraints)


def test_batched_lp_constraint_collection_matches_wrapper(synthetic_output: Path) -> None:
    db = DuckDB(synthetic_output / "oddsgraph.duckdb")
    try:
        inputs = _collect_coherence_inputs(db)
        node_ids = inputs.event_nodes["world-cup-winner"]
        model = EventModel(
            "world-cup-winner",
            node_ids,
            pytest.importorskip("numpy").array([inputs.current_prices[node_id] for node_id in node_ids]),
            {node_id: idx for idx, node_id in enumerate(node_ids)},
        )
        assert _collect_constraints_from_inputs(inputs, model) == _collect_constraints(db, model)
    finally:
        db.close()


def test_empirical_confidence_counts_equal_errors_as_at_least_observed() -> None:
    errors = [0.1, 0.2, 0.2, 0.4]
    assert _empirical_confidence(errors, 0.2) == pytest.approx(0.25)
    assert _empirical_confidence(errors, 0.3) == pytest.approx(0.75)


def test_sql_calibration_confidence_counts_equal_errors_as_at_least_observed(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "calibration.duckdb")
    try:
        db.execute("""
            CREATE TABLE candidate_edges_v AS
            SELECT
                range AS sample_idx,
                'sample_src_' || range::VARCHAR AS src_node_id,
                'sample_dst_' || range::VARCHAR AS dst_node_id,
                'complement' AS candidate_type
            FROM range(50);

            CREATE TABLE aligned_edges AS
            SELECT
                src_node_id,
                dst_node_id,
                candidate_type,
                CASE
                    WHEN sample_idx < 10 THEN 0.1
                    WHEN sample_idx < 30 THEN 0.2
                    ELSE 0.4
                END AS complement_error_raw
            FROM candidate_edges_v;

            CREATE TABLE scored_edges_v AS
            SELECT
                'target_src' AS src_node_id,
                'target_dst' AS dst_node_id,
                'complement' AS candidate_type,
                'complement' AS edge_type,
                'same_market' AS edge_basis,
                0.0 AS confidence,
                0.2 AS score,
                0.2 AS violation_score,
                1000::BIGINT AS overlap_minutes,
                0.5 AS current_p_src,
                0.5 AS current_p_dst,
                0.5 AS mean_p_src,
                0.5 AS mean_p_dst,
                'm1' AS market_id_src,
                'm1' AS market_id_dst,
                'event-1' AS event_slug_src,
                'event-1' AS event_slug_dst,
                'test edge' AS evidence,
                0.2 AS complement_error_raw,
                NULL::DOUBLE AS equivalence_error_raw,
                NULL::DOUBLE AS implication_violation_raw,
                NULL::DOUBLE AS exclusion_violation_raw
            UNION ALL
            SELECT
                'float_src' AS src_node_id,
                'float_dst' AS dst_node_id,
                'implication' AS candidate_type,
                'implies' AS edge_type,
                'price_only' AS edge_basis,
                0.0 AS confidence,
                0.001 AS score,
                0.001 AS violation_score,
                1000::BIGINT AS overlap_minutes,
                0.225 AS current_p_src,
                0.205 AS current_p_dst,
                0.2 AS mean_p_src,
                0.3 AS mean_p_dst,
                'm2' AS market_id_src,
                'm3' AS market_id_dst,
                'event-2' AS event_slug_src,
                'event-2' AS event_slug_dst,
                'float boundary edge' AS evidence,
                NULL::DOUBLE AS complement_error_raw,
                NULL::DOUBLE AS equivalence_error_raw,
                0.001 AS implication_violation_raw,
                NULL::DOUBLE AS exclusion_violation_raw;
        """)

        apply_calibration_confidence(db, default_thresholds())

        confidence = float(db.scalar("SELECT confidence FROM scored_edges_v WHERE src_node_id = 'target_src'") or 0)
        assert confidence == pytest.approx(0.2)
        price_edges = int(db.scalar("SELECT count(*) FROM price_edges_v WHERE src_node_id = 'float_src'") or 0)
        assert price_edges == 1
    finally:
        db.close()


def test_evaluation_with_resolutions(synthetic_input: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    resolutions = tmp_path / "resolutions.parquet"
    write_synthetic_resolutions(resolutions)
    build(synthetic_input, out, resolutions_path=resolutions)
    assert (out / "evaluation.parquet").exists()
    assert (out / "reports" / "evaluation.md").exists()
    db = DuckDB()
    try:
        rows = db.rows(f"DESCRIBE SELECT * FROM read_parquet('{q(out / 'evaluation.parquet')}')")
        assert [row["column_name"] for row in rows] == ARTIFACT_COLUMNS["evaluation.parquet"]
    finally:
        db.close()


def test_build_can_skip_prices_and_keep_query_artifacts(
    synthetic_input: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out"
    build(synthetic_input, out, write_prices=False)

    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["build_options"]["write_prices"] is False
    assert "prices.parquet" not in manifest["artifacts"]
    assert not (out / "prices.parquet").exists()

    assert main(["search", "--out", str(out), "--query", "Equivalent A"]) == 0
    assert "Will Equivalent A happen?" in capsys.readouterr().out
    assert main(["nodes", "--out", str(out), "--top", "3"]) == 0
    assert "node_id" in capsys.readouterr().out
    assert main(["edges", "--out", str(out), "--top", "3"]) == 0
    assert "edge_type" in capsys.readouterr().out
    assert main(["explain", "--out", str(out), "--node", "comp:Yes"]) == 0
    assert "Same-Market Constraint" in capsys.readouterr().out


def test_build_can_skip_coherence_and_keep_conditionals(
    synthetic_input: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out"
    build(synthetic_input, out, solve_coherence=False)

    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["build_options"]["solve_coherence"] is False
    assert "coherence.parquet" not in manifest["artifacts"]
    assert "coherence_repairs.parquet" not in manifest["artifacts"]
    assert not (out / "coherence.parquet").exists()
    assert not (out / "coherence_repairs.parquet").exists()

    db = DuckDB()
    try:
        global_violations = int(db.scalar(f"""
            SELECT count(*)
            FROM read_parquet('{q(out / "violations.parquet")}')
            WHERE violation_type = 'global_incoherence'
        """))
        assert global_violations == 0
    finally:
        db.close()

    assert main(["violations", "--out", str(out), "--top", "5"]) == 0
    assert "violation_type" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "comp:Yes", "--b", "comp:No"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["coherence", "--out", str(out), "--top", "5"]) == 1
    assert "rebuild without --skip-coherence" in capsys.readouterr().err
    (out / "coherence.parquet").write_text("stale\n", encoding="utf-8")
    assert main(["coherence", "--out", str(out), "--top", "5"]) == 1
    assert "rebuild without --skip-coherence" in capsys.readouterr().err


def test_cli_build_can_skip_prices_and_coherence(synthetic_input: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main([
        "build",
        "--input", str(synthetic_input),
        "--out", str(out),
        "--skip-prices",
        "--skip-coherence",
    ]) == 0

    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["build_options"] == {
        "fast_graph": False,
        "graph_lookback_days": 30,
        "solve_coherence": False,
        "write_prices": False,
    }
    assert "prices.parquet" not in manifest["artifacts"]
    assert "coherence.parquet" not in manifest["artifacts"]
    assert "coherence_repairs.parquet" not in manifest["artifacts"]
    assert not (out / "prices.parquet").exists()
    assert not (out / "coherence.parquet").exists()
    assert not (out / "coherence_repairs.parquet").exists()


def test_failed_build_removes_success_manifest(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    out = tmp_path / "out"
    out.mkdir()
    (out / "build_manifest.json").write_text("old\n", encoding="utf-8")
    db = DuckDB(tmp_path / "bad.duckdb")
    try:
        db.execute(f"COPY (SELECT 'm1' AS market_id) TO '{q(path)}' (FORMAT PARQUET)")
    finally:
        db.close()

    with pytest.raises(ValueError, match="missing required columns"):
        build(path, out)
    assert not (out / "build_manifest.json").exists()


def test_cli_smoke(synthetic_input: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"

    assert main(["build", "--input", str(synthetic_input), "--out", str(out)]) == 0
    assert main(["search", "--out", str(out), "--query", "Equivalent A"]) == 0
    assert "Will Equivalent A happen?" in capsys.readouterr().out
    assert main(["coherence", "--out", str(out), "--top", "5"]) == 0
    assert "incoherence_distance" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "comp:Yes", "--b", "comp:No"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "NOT(Will Complement pass?)", "--b", "comp:Yes"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "Alpha", "--b", "comp:Yes"]) == 1
    captured = capsys.readouterr()
    assert "Ambiguous node query" in captured.err
    assert "Candidates:" in captured.err
    assert main(["evaluate", "--out", str(out)]) == 1
    assert "rebuild with --resolutions" in capsys.readouterr().err
    assert main(["benchmark-summary", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "runtime_seconds:" in captured.out
    assert "top_stage_timings:" in captured.out


def test_token_minute_prices_choose_latest_timestamp_per_minute(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "dedupe.duckdb")
    try:
        db.execute("""
            CREATE TABLE input_prices AS
            SELECT *
            FROM (VALUES
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(1), 1::BIGINT, 0::BIGINT, 0.40),
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(45), 45::BIGINT, 0::BIGINT, 0.45),
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(75), 75::BIGINT, 60::BIGINT, 0.50),
                ('m1', 1, 'b', 'Question A', 'No', 'event-1', true, false, 1.0, to_timestamp(2), 2::BIGINT, 0::BIGINT, 0.60),
                ('m1', 1, 'b', 'Question A', 'No', 'event-1', true, false, 1.0, to_timestamp(55), 55::BIGINT, 0::BIGINT, 0.55)
            ) AS t(
                market_id,
                outcome_index,
                clob_token_id,
                question,
                outcome_label,
                event_slug,
                is_active,
                is_closed,
                market_volume_usd,
                odds_timestamp,
                odds_timestamp_epoch,
                odds_minute_epoch,
                price
            );

            CREATE TABLE token_minute_reference AS
            SELECT
                market_id,
                outcome_index,
                clob_token_id,
                question,
                outcome_label,
                event_slug,
                is_active,
                is_closed,
                market_volume_usd,
                odds_timestamp,
                odds_timestamp_epoch,
                odds_minute_epoch,
                price
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
        """)

        _create_token_minute_prices(db)

        actual = db.rows("""
            SELECT * FROM token_minute_prices
            ORDER BY clob_token_id, odds_minute_epoch
        """)
        expected = db.rows("""
            SELECT * FROM token_minute_reference
            ORDER BY clob_token_id, odds_minute_epoch
        """)
        assert actual == expected
    finally:
        db.close()


def test_fast_graph_mode_keeps_query_artifacts(
    synthetic_input: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out"
    assert main([
        "build",
        "--input", str(synthetic_input),
        "--out", str(out),
        "--fast-graph",
        "--graph-lookback-days", "1",
    ]) == 0

    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["build_options"] == {
        "fast_graph": True,
        "graph_lookback_days": 1,
        "solve_coherence": False,
        "write_prices": False,
    }
    assert manifest["stats"]["history_mode"] == "fast_graph_lookback"
    assert "prices.parquet" not in manifest["artifacts"]
    assert "coherence.parquet" not in manifest["artifacts"]
    assert not (out / "prices.parquet").exists()
    assert not (out / "coherence.parquet").exists()

    db = DuckDB()
    try:
        active_minutes = int(db.scalar(f"""
            SELECT active_minutes
            FROM read_parquet('{q(out / "nodes.parquet")}')
            WHERE node_id = 'comp:Yes'
        """) or 0)
    finally:
        db.close()
    assert active_minutes == 1

    assert main(["search", "--out", str(out), "--query", "Complement"]) == 0
    assert "Will Complement pass?" in capsys.readouterr().out
    assert main(["edges", "--out", str(out), "--top", "3"]) == 0
    assert "edge_type" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "comp:Yes", "--b", "comp:No"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["explain", "--out", str(out), "--node", "comp:Yes"]) == 0
    assert "Same-Market Constraint" in capsys.readouterr().out


def test_graph_lookback_days_requires_fast_graph(synthetic_input: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([
        "build",
        "--input", str(synthetic_input),
        "--out", str(tmp_path / "out"),
        "--graph-lookback-days", "1",
    ]) == 1
    assert "requires --fast-graph" in capsys.readouterr().err

    assert main([
        "build",
        "--input", str(synthetic_input),
        "--out", str(tmp_path / "out"),
        "--fast-graph",
        "--graph-lookback-days", "0",
    ]) == 1
    assert "must be positive" in capsys.readouterr().err


def test_stage_invariants_report_duplicate_token_minutes(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "invariants.duckdb")
    try:
        db.execute("""
            CREATE TABLE token_minute_prices AS
            SELECT 'a' AS clob_token_id, 0::BIGINT AS odds_minute_epoch
            UNION ALL
            SELECT 'a', 0::BIGINT
        """)
        with pytest.raises(RuntimeError, match="duplicate token-minute rows: 1"):
            _validate_token_minute_prices(db)
    finally:
        db.close()


def test_stage_invariants_report_duplicate_final_edges(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "edge_invariants.duckdb")
    try:
        db.execute("""
            CREATE TABLE logic_edges_v AS
            SELECT 'a' AS src_node_id, 'b' AS dst_node_id, 'implies' AS edge_type
            UNION ALL
            SELECT 'a', 'b', 'implies';

            CREATE TABLE price_edges_v AS
            SELECT 'c' AS src_node_id, 'd' AS dst_node_id, 'equivalent' AS edge_type
            WHERE false;
        """)
        with pytest.raises(RuntimeError, match="duplicate logic edges: 1"):
            _validate_final_edge_invariants(db)
    finally:
        db.close()


def test_cli_explain_smoke(synthetic_output: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "--out", str(synthetic_output), "--node", "comp:Yes"]) == 0
    captured = capsys.readouterr()
    assert "Same-Market Constraint" in captured.out
    assert "comp:No" in captured.out

    assert main(["explain", "--out", str(synthetic_output), "--node", "Messi"]) == 0
    captured = capsys.readouterr()
    assert "Top goalscorer? :: Messi" in captured.out

    assert main(["explain", "--out", str(synthetic_output), "--node", "Alpha"]) == 1
    captured = capsys.readouterr()
    assert "Ambiguous node query" in captured.err
    assert "Candidates:" in captured.err

    assert main([
        "explain-edge",
        "--out", str(synthetic_output),
        "--src", "comp:No",
        "--dst", "comp:Yes",
        "--edge-type", "complement",
    ]) == 0
    captured = capsys.readouterr()
    assert "Logic Edge" in captured.out
    assert "same_market" in captured.out

    assert main([
        "explain-edge",
        "--out", str(synthetic_output),
        "--src", "eq_a:Yes",
        "--dst", "eq_b:Yes",
        "--edge-type", "equivalent",
    ]) == 0
    captured = capsys.readouterr()
    assert "Price-Only Edge" in captured.out
    assert "price_only" in captured.out

    assert main([
        "explain-edge",
        "--out", str(synthetic_output),
        "--src", "alpha_final:Yes",
        "--dst", "winner_alpha:Yes",
        "--edge-type", "implies",
    ]) == 0
    captured = capsys.readouterr()
    assert "stage_progression_rule" not in captured.out


def test_search_treats_like_wildcards_and_quotes_as_literal_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "cli_fixture"
    _write_cli_param_fixture(out)

    assert main(["search", "--out", str(out), "--query", "%"]) == 0
    captured = capsys.readouterr()
    assert "literal%_node" in captured.out
    assert "quote'node" not in captured.out

    assert main(["search", "--out", str(out), "--query", "_"]) == 0
    captured = capsys.readouterr()
    assert "literal%_node" in captured.out
    assert "quote'node" not in captured.out

    assert main(["search", "--out", str(out), "--query", "quote'"]) == 0
    captured = capsys.readouterr()
    assert "quote'node" in captured.out


def test_condition_and_explain_accept_quoted_node_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "cli_fixture"
    _write_cli_param_fixture(out)

    assert main([
        "condition",
        "--out", str(out),
        "--a", "quote'node",
        "--b", "literal%_node",
    ]) == 0
    captured = capsys.readouterr()
    assert "quoted_fixture" in captured.out

    assert main(["explain", "--out", str(out), "--node", "quote'node"]) == 0
    captured = capsys.readouterr()
    assert "Same-Market Constraint" in captured.out
    assert "literal%_node" in captured.out


def _write_input(path: Path, rows: list[tuple[Any, ...]]) -> None:
    db = DuckDB(path.with_suffix(".duckdb"))
    try:
        db.execute(f"""
            COPY (
                WITH rows(
                    market_id,
                    outcome_index,
                    clob_token_id,
                    question,
                    outcome_label,
                    event_slug,
                    is_active,
                    is_closed,
                    market_volume_usd,
                    odds_epoch,
                    price
                ) AS (
                    VALUES {_values(rows)}
                )
                SELECT
                    market_id,
                    outcome_index,
                    clob_token_id,
                    question,
                    outcome_label,
                    event_slug,
                    is_active,
                    is_closed,
                    market_volume_usd,
                    to_timestamp(odds_epoch) AS ODDS_TIMESTAMP,
                    odds_epoch::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    price
                FROM rows
            ) TO '{q(path)}' (FORMAT PARQUET)
        """)
    finally:
        db.close()


def _values(rows: list[tuple[Any, ...]]) -> str:
    return ", ".join("(" + ", ".join(sql_literal(value) for value in row) + ")" for row in rows)


def _write_cli_param_fixture(out: Path) -> None:
    out.mkdir()
    db = DuckDB(out / "fixture.duckdb")
    try:
        _copy_query(db, out, "nodes.parquet", """
            SELECT *
            FROM (VALUES
                (
                    'quote''node',
                    'm_cli',
                    0,
                    'Will Quote''s fixture resolve?',
                    'Quoted',
                    'cli-event',
                    'unknown',
                    0.40,
                    0.40,
                    120,
                    'Quote''s exact proposition'
                ),
                (
                    'literal%_node',
                    'm_cli',
                    1,
                    'Will literal %_ fixture resolve?',
                    'Literal',
                    'cli-event',
                    'unknown',
                    0.60,
                    0.60,
                    120,
                    'Literal %_ proposition'
                )
            ) AS t(
                node_id,
                market_id,
                outcome_index,
                question,
                outcome_label,
                event_slug,
                market_family,
                current_price,
                mean_price,
                active_minutes,
                canonical_proposition
            )
        """)
        _copy_query(db, out, "market_groups.parquet", """
            SELECT
                'm_cli' AS market_id,
                1.0::DOUBLE AS current_sum_price,
                1.0::DOUBLE AS mean_sum_price
        """)
        _copy_query(db, out, "logic_edges.parquet", _empty_edge_query())
        _copy_query(db, out, "price_edges.parquet", _empty_edge_query())
        _copy_query(db, out, "violations.parquet", """
            SELECT
                NULL::VARCHAR AS violation_type,
                NULL::DOUBLE AS severity,
                NULL::DOUBLE AS current_gap,
                NULL::DOUBLE AS mean_gap,
                NULL::VARCHAR AS src_node_id,
                NULL::VARCHAR AS dst_node_id,
                NULL::VARCHAR AS description
            WHERE false
        """)
        _copy_query(db, out, "conditional_edges.parquet", """
            SELECT
                'quote''node' AS a_node_id,
                'literal%_node' AS b_node_id,
                0.25::DOUBLE AS p_a_given_b,
                0.0::DOUBLE AS lower_bound,
                1.0::DOUBLE AS upper_bound,
                'quoted_fixture' AS method,
                0.90::DOUBLE AS confidence,
                to_timestamp(0) AS as_of_ts,
                'parameter fixture' AS evidence
        """)
    finally:
        db.close()


def _copy_query(db: DuckDB, out: Path, artifact: str, sql: str) -> None:
    db.execute(f"COPY ({sql}) TO '{q(out / artifact)}' (FORMAT PARQUET)")


def _empty_edge_query() -> str:
    return """
        SELECT
            NULL::VARCHAR AS edge_type,
            NULL::VARCHAR AS edge_basis,
            NULL::DOUBLE AS confidence,
            NULL::DOUBLE AS score,
            NULL::BIGINT AS overlap_minutes,
            NULL::VARCHAR AS src_node_id,
            NULL::VARCHAR AS dst_node_id,
            NULL::VARCHAR AS evidence
        WHERE false
    """
