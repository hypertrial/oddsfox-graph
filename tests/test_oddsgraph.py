from __future__ import annotations

from pathlib import Path

import pytest

from oddsgraph.build import build
from oddsgraph.cli import main
from oddsgraph.queries import DuckDB, q
from oddsgraph.schema import validate_input


ARTIFACTS = {
    "nodes.parquet",
    "prices.parquet",
    "market_groups.parquet",
    "candidate_edges.parquet",
    "logic_edges.parquet",
    "constraint_hyperedges.parquet",
    "conditional_edges.parquet",
    "violations.parquet",
}


def test_schema_rejects_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    db = DuckDB(tmp_path / "bad.duckdb")
    try:
        db.execute(f"COPY (SELECT 'm1' AS market_id) TO '{q(path)}' (FORMAT PARQUET)")
        with pytest.raises(ValueError, match="missing required columns"):
            validate_input(db, path)
    finally:
        db.close()


def test_build_outputs_artifacts_and_core_logic(tmp_path: Path) -> None:
    input_path = _fixture(tmp_path)
    out = tmp_path / "out"

    stats = build(input_path, out)

    assert stats["markets"] == 11
    assert stats["tokens"] == 22
    assert ARTIFACTS <= {p.name for p in out.glob("*.parquet")}
    assert (out / "reports" / "summary.md").read_text()

    db = DuckDB()
    try:
        nodes = db.rows(f"""
            SELECT outcome_label, canonical_proposition
            FROM read_parquet('{q(out / "nodes.parquet")}')
            WHERE market_id = 'named'
            ORDER BY outcome_label
        """)
        assert nodes == [
            {"outcome_label": "Messi", "canonical_proposition": "Top goalscorer? :: Messi"},
            {"outcome_label": "Ronaldo", "canonical_proposition": "Top goalscorer? :: Ronaldo"},
        ]

        current_sum = float(db.scalar(f"""
            SELECT current_sum_price
            FROM read_parquet('{q(out / "market_groups.parquet")}')
            WHERE market_id = 'stale'
        """))
        assert current_sum == pytest.approx(1.0)
        assert int(db.scalar(f"SELECT count(*) FROM read_parquet('{q(out / 'constraint_hyperedges.parquet')}')")) == 11
        duplicate_candidates = int(db.scalar(f"""
            SELECT count(*)
            FROM (
                SELECT src_node_id, dst_node_id, candidate_type
                FROM read_parquet('{q(out / 'candidate_edges.parquet')}')
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            )
        """))
        assert duplicate_candidates == 0

        violations = db.rows(f"""
            SELECT violation_type
            FROM read_parquet('{q(out / "violations.parquet")}')
            WHERE market_id_src = 'bad'
        """)
        assert violations == [{"violation_type": "complement_violation"}]

        logic_path = q(out / "logic_edges.parquet")
        edge_types = {
            row["edge_type"]
            for row in db.rows(f"SELECT DISTINCT edge_type FROM read_parquet('{logic_path}')")
        }
        assert {"complement", "equivalent", "implies", "mutually_exclusive"} <= edge_types

        methods = {
            row["method"]
            for row in db.rows(
                f"SELECT DISTINCT method FROM read_parquet('{q(out / 'conditional_edges.parquet')}')"
            )
        }
        assert {"exact_complement", "exact_implication", "exact_implication_reverse", "exact_exclusion", "bounded_frechet"} <= methods
        reverse_complement = db.rows(f"""
            SELECT method, p_a_given_b
            FROM read_parquet('{q(out / 'conditional_edges.parquet')}')
            WHERE a_node_id = 'base:No' AND b_node_id = 'base:Yes'
        """)
        assert reverse_complement == [{"method": "exact_complement", "p_a_given_b": 0.0}]
    finally:
        db.close()


def test_cli_smoke(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_path = _fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["build", "--input", str(input_path), "--out", str(out)]) == 0
    assert main(["search", "--out", str(out), "--query", "Brazil"]) == 0
    assert "Will Brazil win?" in capsys.readouterr().out


def _fixture(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.parquet"
    db = DuckDB(tmp_path / "fixture.duckdb")
    try:
        db.execute(
            f"""
            CREATE TABLE fixture AS
            WITH minute AS (
                SELECT range AS i, epoch_ms(TIMESTAMP '2026-01-01 00:00:00' + range * INTERVAL 1 MINUTE) / 1000 AS ts
                FROM range(1000)
            ),
            market_defs(market_id, question, event_slug, yes_price, no_price, volume) AS (
                VALUES
                    ('base', 'Will Brazil win?', 'winner', 0.30, 0.70, 20000.0),
                    ('dup', 'Will Brazil win?', 'winner', 0.31, 0.69, 20000.0),
                    ('superset', 'Will a South American team win?', 'winner', 0.50, 0.50, 20000.0),
                    ('arg', 'Will Argentina win?', 'winner', 0.20, 0.80, 20000.0),
                    ('other', 'Will France win?', 'winner', 0.40, 0.60, 20000.0),
                    ('reject_equiv', 'Will Germany win?', 'winner', 0.80, 0.20, 20000.0),
                    ('reject_impl', 'Will Spain win?', 'winner', 0.25, 0.75, 20000.0),
                    ('reject_excl', 'Will Portugal win?', 'winner', 0.90, 0.10, 20000.0)
            ),
            binary_rows AS (
                SELECT
                    market_id,
                    outcome_index,
                    market_id || ':' || outcome_label AS clob_token_id,
                    question,
                    outcome_label,
                    event_slug,
                    true AS is_active,
                    false AS is_closed,
                    volume AS market_volume_usd,
                    to_timestamp(ts) AS ODDS_TIMESTAMP,
                    ts::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS price
                FROM market_defs
                CROSS JOIN minute
                CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label)
            ),
            named_rows AS (
                SELECT
                    'named' AS market_id,
                    outcome_index,
                    'named:' || outcome_label AS clob_token_id,
                    'Top goalscorer?' AS question,
                    outcome_label,
                    'named-event' AS event_slug,
                    true AS is_active,
                    false AS is_closed,
                    1.0 AS market_volume_usd,
                    to_timestamp(ts) AS ODDS_TIMESTAMP,
                    ts::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    CASE outcome_label WHEN 'Messi' THEN 0.55 ELSE 0.45 END AS price
                FROM (SELECT * FROM minute LIMIT 3)
                CROSS JOIN (VALUES (0, 'Messi'), (1, 'Ronaldo')) AS o(outcome_index, outcome_label)
            ),
            stale_rows AS (
                SELECT 'stale', 0, 'stale:Yes', 'Will stale pass?', 'Yes', 'stale-event', true, false, 1.0,
                    to_timestamp(1), 1::BIGINT, 0.2
                UNION ALL SELECT 'stale', 1, 'stale:No', 'Will stale pass?', 'No', 'stale-event', true, false, 1.0,
                    to_timestamp(1), 1::BIGINT, 0.8
                UNION ALL SELECT 'stale', 0, 'stale:Yes', 'Will stale pass?', 'Yes', 'stale-event', true, false, 1.0,
                    to_timestamp(2), 2::BIGINT, 0.9
            ),
            bad_rows AS (
                SELECT 'bad', outcome_index, 'bad:' || outcome_label, 'Will bad sum?', outcome_label, 'bad-event', true, false, 1.0,
                    to_timestamp(ts), ts::BIGINT, CASE outcome_label WHEN 'Yes' THEN 0.70 ELSE 0.70 END
                FROM (SELECT * FROM minute LIMIT 20)
                CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label)
            )
            SELECT * FROM binary_rows
            UNION ALL SELECT * FROM named_rows
            UNION ALL SELECT * FROM stale_rows
            UNION ALL SELECT * FROM bad_rows;

            COPY fixture TO '{q(path)}' (FORMAT PARQUET);
            """
        )
        return path
    finally:
        db.close()
