from __future__ import annotations

import csv
from pathlib import Path

import pytest

from oddsfox_graph.build import build
from oddsfox_graph.queries import DuckDB, q
from tests.synthetic import write_mini_wc2026_oracle_input


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_ORACLE = ROOT / "tests" / "fixtures" / "synthetic_edge_oracle.csv"
WC2026_ORACLE = ROOT / "tests" / "fixtures" / "wc2026_edge_oracle.csv"
WC2026_OUT = ROOT / "output" / "wc2026"


def _oracle_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _edge_set(out: Path) -> set[tuple[str, str, str]]:
    db = DuckDB()
    try:
        rows = db.rows(
            f"""
            SELECT src_node_id, dst_node_id, edge_type
            FROM read_parquet('{q(out / "logic_edges.parquet")}')
            """
        )
    finally:
        db.close()
    return {(row["src_node_id"], row["dst_node_id"], row["edge_type"]) for row in rows}


def _scalar(db: DuckDB, sql: str) -> int:
    return int(db.scalar(sql) or 0)


def _assert_logic_invariants(out: Path) -> None:
    db = DuckDB()
    logic = q(out / "logic_edges.parquet")
    try:
        checks = {
            "self edges": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE src_node_id = dst_node_id
            """,
            "duplicate typed edges": f"""
                SELECT count(*)
                FROM (
                    SELECT src_node_id, dst_node_id, edge_type
                    FROM read_parquet('{logic}')
                    GROUP BY 1, 2, 3
                    HAVING count(*) > 1
                )
            """,
            "cross-market complements": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE edge_type = 'complement' AND market_id_src != market_id_dst
            """,
            "bad complement basis": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE edge_type = 'complement' AND edge_basis != 'same_market'
            """,
            "bad equivalence basis": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE edge_type = 'equivalent' AND edge_basis != 'exact_duplicate'
            """,
            "bad implication basis": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE edge_type = 'implies' AND edge_basis != 'stage_progression_rule'
            """,
            "bad exclusion basis": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE edge_type = 'mutually_exclusive'
                    AND edge_basis NOT IN ('single_winner_family', 'same_market')
            """,
            "null required metadata": f"""
                SELECT count(*) FROM read_parquet('{logic}')
                WHERE confidence IS NULL
                    OR market_id_src IS NULL
                    OR market_id_dst IS NULL
                    OR event_slug_src IS NULL
                    OR event_slug_dst IS NULL
            """,
        }
        failures = {name: _scalar(db, sql) for name, sql in checks.items()}
    finally:
        db.close()
    assert failures == {name: 0 for name in checks}


def _resolve_node(db: DuckDB, out: Path, query: str) -> str:
    nodes = q(out / "nodes.parquet")
    exact = db.rows(
        f"""
        SELECT node_id
        FROM read_parquet('{nodes}')
        WHERE node_id = '{q(query)}'
        """
    )
    if exact:
        assert len(exact) == 1, query
        return exact[0]["node_id"]
    matches = db.rows(
        f"""
        SELECT node_id
        FROM read_parquet('{nodes}')
        WHERE lower(canonical_proposition) LIKE lower('%{q(query)}%')
            OR lower(question) LIKE lower('%{q(query)}%')
        """
    )
    assert len(matches) == 1, f"{query!r} resolved to {len(matches)} nodes"
    return matches[0]["node_id"]


def _wc2026_output_or_skip() -> Path:
    if not (WC2026_OUT / "logic_edges.parquet").exists():
        pytest.skip("output/wc2026 is not present")
    return WC2026_OUT


@pytest.fixture(scope="session")
def mini_wc2026_output(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("mini_wc2026")
    input_path = base / "mini_wc2026.parquet"
    out = base / "out"
    write_mini_wc2026_oracle_input(input_path)
    build(input_path, out)
    return out


def test_edge_oracle_required_edges_present(synthetic_output: Path) -> None:
    edges = _edge_set(synthetic_output)
    required = [row for row in _oracle_rows(SYNTHETIC_ORACLE) if row["expectation"] == "required"]
    missing = [
        row
        for row in required
        if (row["src_node_id"], row["dst_node_id"], row["edge_type"]) not in edges
    ]
    assert missing == []


def test_edge_oracle_forbidden_edges_absent(synthetic_output: Path) -> None:
    edges = _edge_set(synthetic_output)
    forbidden = [row for row in _oracle_rows(SYNTHETIC_ORACLE) if row["expectation"] == "forbidden"]
    present = [
        row
        for row in forbidden
        if (row["src_node_id"], row["dst_node_id"], row["edge_type"]) in edges
    ]
    assert present == []


def test_implication_is_directional(synthetic_output: Path) -> None:
    edges = _edge_set(synthetic_output)
    assert ("winner_alpha:Yes", "alpha_final:Yes", "implies") in edges
    assert ("alpha_final:Yes", "winner_alpha:Yes", "implies") not in edges


def test_logic_invariants_on_synthetic_build(synthetic_output: Path) -> None:
    _assert_logic_invariants(synthetic_output)


def _assert_wc2026_oracle(out: Path) -> None:
    edges = _edge_set(out)
    db = DuckDB()
    try:
        resolved = []
        for row in _oracle_rows(WC2026_ORACLE):
            resolved.append(
                {
                    **row,
                    "src_node_id": _resolve_node(db, out, row["src_query"]),
                    "dst_node_id": _resolve_node(db, out, row["dst_query"]),
                }
            )
    finally:
        db.close()
    required_missing = [
        row
        for row in resolved
        if row["expectation"] == "required"
        and (row["src_node_id"], row["dst_node_id"], row["edge_type"]) not in edges
    ]
    forbidden_present = [
        row
        for row in resolved
        if row["expectation"] == "forbidden"
        and (row["src_node_id"], row["dst_node_id"], row["edge_type"]) in edges
    ]
    assert required_missing == []
    assert forbidden_present == []


def test_wc2026_oracle_on_mini_fixture(mini_wc2026_output: Path) -> None:
    _assert_wc2026_oracle(mini_wc2026_output)


@pytest.mark.full_output
def test_wc2026_oracle_if_available() -> None:
    _assert_wc2026_oracle(_wc2026_output_or_skip())
