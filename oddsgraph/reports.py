from __future__ import annotations

from pathlib import Path

from .queries import DuckDB


def write_reports(db: DuckDB, out_dir: Path, stats: dict[str, object]) -> None:
    reports = out_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _write(reports / "summary.md", _summary(stats))
    _write(reports / "top_complement_violations.md", _query_report(db, "Top Complement Violations", """
        SELECT violation_id, severity, current_gap, mean_gap, src_node_id, dst_node_id
        FROM violations_v
        WHERE violation_type = 'complement_violation'
        ORDER BY current_gap DESC, mean_gap DESC
        LIMIT 50
    """))
    _write(reports / "strongest_implications.md", _query_report(db, "Strongest Implications", """
        SELECT src_node_id, dst_node_id, confidence, violation_score, overlap_minutes, current_p_src, current_p_dst
        FROM logic_edges_v
        WHERE edge_type = 'implies'
        ORDER BY confidence DESC, overlap_minutes DESC
        LIMIT 50
    """))
    _write(reports / "strongest_exclusions.md", _query_report(db, "Strongest Exclusions", """
        SELECT src_node_id, dst_node_id, confidence, violation_score, overlap_minutes, current_p_src, current_p_dst
        FROM logic_edges_v
        WHERE edge_type = 'mutually_exclusive'
        ORDER BY confidence DESC, overlap_minutes DESC
        LIMIT 50
    """))
    _write(reports / "duplicate_candidates.md", _query_report(db, "Duplicate Candidates", """
        SELECT src_node_id, dst_node_id, candidate_source, candidate_score, market_id_src, market_id_dst
        FROM candidate_edges_v
        WHERE candidate_source = 'same_question_text_exact'
        ORDER BY candidate_score DESC
        LIMIT 50
    """))
    _write(reports / "conditional_examples.md", _query_report(db, "Conditional Examples", """
        SELECT a_node_id, b_node_id, method, p_a_given_b, lower_bound, upper_bound, confidence
        FROM conditional_edges_v
        ORDER BY confidence DESC, method
        LIMIT 50
    """))


def _summary(stats: dict[str, object]) -> str:
    lines = ["# oddsgraph build summary", ""]
    for key in (
        "input_rows",
        "markets",
        "tokens",
        "time_range_start",
        "time_range_end",
        "active_markets",
        "closed_markets",
        "candidate_edges",
        "logic_edges",
        "violations",
        "runtime_seconds",
    ):
        lines.append(f"- **{key}:** {stats.get(key)}")
    return "\n".join(lines) + "\n"


def _query_report(db: DuckDB, title: str, sql: str) -> str:
    rows = db.rows(sql)
    lines = [f"# {title}", ""]
    if not rows:
        return "\n".join(lines + ["No rows.", ""])
    cols = list(rows[0])
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(col)) for col in cols) + " |")
    return "\n".join(lines) + "\n"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|") if value is not None else ""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
