from __future__ import annotations

from pathlib import Path

from .queries import DuckDB


def write_reports(db: DuckDB, out_dir: Path, stats: dict[str, object]) -> None:
    reports = out_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _write(reports / "summary.md", _summary(stats))
    _write(
        reports / "strongest_implications.md",
        _query_report(
            db,
            "Implications",
            """
            SELECT
                e.src_node_id,
                e.dst_node_id,
                e.edge_basis,
                s.canonical_proposition AS src_proposition,
                d.canonical_proposition AS dst_proposition,
                e.confidence,
                e.evidence
            FROM logic_edges_v e
            JOIN nodes_v s ON s.node_id = e.src_node_id
            JOIN nodes_v d ON d.node_id = e.dst_node_id
            WHERE e.edge_type = 'implies'
            ORDER BY e.edge_basis, e.src_node_id, e.dst_node_id
            LIMIT 50
            """,
        ),
    )
    _write(
        reports / "strongest_exclusions.md",
        _query_report(
            db,
            "Exclusions",
            """
            SELECT
                e.src_node_id,
                e.dst_node_id,
                e.edge_basis,
                s.canonical_proposition AS src_proposition,
                d.canonical_proposition AS dst_proposition,
                e.confidence,
                e.evidence
            FROM logic_edges_v e
            JOIN nodes_v s ON s.node_id = e.src_node_id
            JOIN nodes_v d ON d.node_id = e.dst_node_id
            WHERE e.edge_type = 'mutually_exclusive'
            ORDER BY e.edge_basis, e.src_node_id, e.dst_node_id
            LIMIT 50
            """,
        ),
    )
    _write(
        reports / "duplicate_edges.md",
        _query_report(
            db,
            "Duplicate Edges",
            """
            SELECT
                e.src_node_id,
                e.dst_node_id,
                e.edge_basis,
                s.canonical_proposition,
                e.market_id_src,
                e.market_id_dst,
                e.evidence
            FROM logic_edges_v e
            JOIN nodes_v s ON s.node_id = e.src_node_id
            WHERE e.edge_type = 'equivalent'
            ORDER BY e.src_node_id, e.dst_node_id
            LIMIT 50
            """,
        ),
    )
    _write(reports / "coverage.md", _coverage_report(db))
    _write(
        reports / "conditional_examples.md",
        _query_report(
            db,
            "Conditional Examples",
            """
            SELECT a_node_id, b_node_id, method, p_a_given_b, confidence, evidence
            FROM conditional_edges_v
            ORDER BY method, a_node_id, b_node_id
            LIMIT 50
            """,
        ),
    )


def _summary(stats: dict[str, object]) -> str:
    lines = ["# oddsfox-graph build summary", ""]
    for key in (
        "input_rows",
        "markets",
        "tokens",
        "active_markets",
        "closed_markets",
        "candidate_edges",
        "logic_edges",
        "derived_edges",
        "conditional_edges",
        "runtime_seconds",
        "time_range_start",
        "time_range_end",
    ):
        if key in stats:
            lines.append(f"- `{key}`: {stats[key]}")
    return "\n".join(lines) + "\n"


def _coverage_report(db: DuckDB) -> str:
    rows = db.rows(
        """
        SELECT edge_basis, edge_type, count(*) AS edge_count
        FROM logic_edges_v
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    )
    lines = ["# Coverage", "", "| edge_basis | edge_type | count |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['edge_basis']} | {row['edge_type']} | {row['edge_count']} |")
    methods = db.rows(
        """
        SELECT method, count(*) AS conditional_count
        FROM conditional_edges_v
        GROUP BY 1
        ORDER BY 1
        """
    )
    lines.extend(["", "## Conditionals", "", "| method | count |", "|---|---|"])
    for row in methods:
        lines.append(f"| {row['method']} | {row['conditional_count']} |")
    return "\n".join(lines) + "\n"


def _query_report(db: DuckDB, title: str, sql: str) -> str:
    rows = db.rows(sql)
    if not rows:
        return f"# {title}\n\nNo rows.\n"
    cols = list(rows[0])
    lines = [f"# {title}", "", "| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
