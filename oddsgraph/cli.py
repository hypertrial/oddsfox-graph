from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build
from .queries import q
from .search import read_rows, resolve_node, search_nodes


EDGE_TYPES = ("complement", "equivalent", "implies", "mutually_exclusive")
EDGE_TO_CANDIDATE = {
    "complement": "complement",
    "equivalent": "equivalence",
    "implies": "implication",
    "mutually_exclusive": "mutual_exclusion",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oddsgraph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("nodes")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--top", type=int, default=50)

    p = sub.add_parser("edges")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--edge-type", default=None)
    p.add_argument("--top", type=int, default=50)

    p = sub.add_parser("price-edges")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--edge-type", default=None)
    p.add_argument("--top", type=int, default=50)

    p = sub.add_parser("violations")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--top", type=int, default=50)

    p = sub.add_parser("condition")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)

    p = sub.add_parser("explain")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--node", required=True)

    p = sub.add_parser("explain-edge")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    p.add_argument("--edge-type", required=True, choices=EDGE_TYPES)

    p = sub.add_parser("search")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--query", required=True)
    p.add_argument("--top", type=int, default=20)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "build":
            stats = build(args.input, args.out)
            for key, value in stats.items():
                print(f"{key}: {value}")
        elif args.cmd == "nodes":
            _print_rows(read_rows(args.out, "nodes.parquet", f"""
                SELECT node_id, market_id, outcome_label, current_price, canonical_proposition
                FROM read_parquet('{{path}}')
                ORDER BY market_volume_usd DESC, current_price DESC NULLS LAST
                LIMIT {args.top}
            """))
        elif args.cmd == "edges":
            edge_filter = f"WHERE edge_type = '{q(args.edge_type)}'" if args.edge_type else ""
            _print_rows(read_rows(args.out, "logic_edges.parquet", f"""
                SELECT edge_type, edge_basis, confidence, score, overlap_minutes, src_node_id, dst_node_id
                FROM read_parquet('{{path}}')
                {edge_filter}
                ORDER BY confidence DESC, overlap_minutes DESC
                LIMIT {args.top}
            """))
        elif args.cmd == "price-edges":
            edge_filter = f"WHERE edge_type = '{q(args.edge_type)}'" if args.edge_type else ""
            _print_rows(read_rows(args.out, "price_edges.parquet", f"""
                SELECT edge_type, edge_basis, confidence, score, overlap_minutes, src_node_id, dst_node_id
                FROM read_parquet('{{path}}')
                {edge_filter}
                ORDER BY confidence DESC, overlap_minutes DESC
                LIMIT {args.top}
            """))
        elif args.cmd == "violations":
            _print_rows(read_rows(args.out, "violations.parquet", f"""
                SELECT violation_type, severity, current_gap, mean_gap, src_node_id, dst_node_id
                FROM read_parquet('{{path}}')
                ORDER BY current_gap DESC, mean_gap DESC
                LIMIT {args.top}
            """))
        elif args.cmd == "condition":
            a = resolve_node(args.out, args.a, require_unique=True)
            b = resolve_node(args.out, args.b, require_unique=True)
            if not a or not b:
                raise ValueError("Could not resolve both nodes")
            _print_rows(read_rows(args.out, "conditional_edges.parquet", f"""
                SELECT *
                FROM read_parquet('{{path}}')
                WHERE a_node_id = '{q(a)}' AND b_node_id = '{q(b)}'
                LIMIT 20
            """))
        elif args.cmd == "explain":
            node = _resolve_required(args.out, args.node)
            _print_explain_node(args.out, node)
        elif args.cmd == "explain-edge":
            src = _resolve_required(args.out, args.src)
            dst = _resolve_required(args.out, args.dst)
            _print_explain_edge(args.out, src, dst, args.edge_type)
        elif args.cmd == "search":
            _print_rows(search_nodes(args.out, args.query, args.top))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _resolve_required(out_dir: Path, text: str) -> str:
    node = resolve_node(out_dir, text, require_unique=True)
    if not node:
        raise ValueError(f"Could not resolve node query {text!r}")
    return node


def _print_explain_node(out_dir: Path, node: str) -> None:
    n = q(node)
    _print_section("Node", read_rows(out_dir, "nodes.parquet", f"""
        SELECT
            node_id,
            market_id,
            outcome_label,
            event_slug,
            market_family,
            current_price,
            mean_price,
            active_minutes,
            canonical_proposition
        FROM read_parquet('{{path}}')
        WHERE node_id = '{n}'
    """))
    _print_section("Same-Market Constraint", read_rows(out_dir, "nodes.parquet", f"""
        SELECT
            n.node_id AS sibling_node_id,
            n.outcome_label,
            n.current_price,
            g.current_sum_price,
            g.mean_sum_price,
            n.canonical_proposition
        FROM read_parquet('{{path}}') n
        JOIN read_parquet('{q(out_dir / "market_groups.parquet")}') g USING (market_id)
        WHERE n.market_id = (
            SELECT market_id FROM read_parquet('{{path}}') WHERE node_id = '{n}'
        )
            AND n.node_id != '{n}'
        ORDER BY n.outcome_index
    """))
    _print_section("Logic Edges", _touching_edges(out_dir, "logic_edges.parquet", node))
    _print_section("Price-Only Edges", _touching_edges(out_dir, "price_edges.parquet", node))
    _print_section("Violations", read_rows(out_dir, "violations.parquet", f"""
        SELECT violation_type, severity, current_gap, mean_gap, src_node_id, dst_node_id, description
        FROM read_parquet('{{path}}')
        WHERE src_node_id = '{n}' OR dst_node_id = '{n}'
        ORDER BY current_gap DESC, mean_gap DESC
        LIMIT 20
    """))
    _print_section("Conditionals", read_rows(out_dir, "conditional_edges.parquet", f"""
        SELECT a_node_id, b_node_id, method, p_a_given_b, lower_bound, upper_bound, confidence
        FROM read_parquet('{{path}}')
        WHERE a_node_id = '{n}' OR b_node_id = '{n}'
        ORDER BY confidence DESC, method
        LIMIT 20
    """))


def _touching_edges(out_dir: Path, artifact: str, node: str) -> list[dict[str, object]]:
    n = q(node)
    return read_rows(out_dir, artifact, f"""
        SELECT edge_type, edge_basis, confidence, score, overlap_minutes, src_node_id, dst_node_id, evidence
        FROM read_parquet('{{path}}')
        WHERE src_node_id = '{n}' OR dst_node_id = '{n}'
        ORDER BY confidence DESC, overlap_minutes DESC
        LIMIT 20
    """)


def _print_explain_edge(out_dir: Path, src: str, dst: str, edge_type: str) -> None:
    edge_where = _edge_where(src, dst, edge_type)
    pair_where = _edge_where(src, dst, "complement")
    conditional_where = (
        f"(a_node_id = '{q(src)}' AND b_node_id = '{q(dst)}')"
        f" OR (a_node_id = '{q(dst)}' AND b_node_id = '{q(src)}')"
    )
    _print_section("Logic Edge", read_rows(out_dir, "logic_edges.parquet", f"""
        SELECT edge_type, edge_basis, confidence, score, violation_score, overlap_minutes,
            current_p_src, current_p_dst, src_node_id, dst_node_id, evidence
        FROM read_parquet('{{path}}')
        WHERE edge_type = '{q(edge_type)}' AND ({edge_where})
        ORDER BY confidence DESC
        LIMIT 20
    """))
    _print_section("Price-Only Edge", read_rows(out_dir, "price_edges.parquet", f"""
        SELECT edge_type, edge_basis, confidence, score, violation_score, overlap_minutes,
            current_p_src, current_p_dst, src_node_id, dst_node_id, evidence
        FROM read_parquet('{{path}}')
        WHERE edge_type = '{q(edge_type)}' AND ({edge_where})
        ORDER BY confidence DESC
        LIMIT 20
    """))
    _print_section("Candidate", read_rows(out_dir, "candidate_edges.parquet", f"""
        SELECT candidate_type, candidate_source, candidate_score, src_node_id, dst_node_id
        FROM read_parquet('{{path}}')
        WHERE candidate_type = '{q(EDGE_TO_CANDIDATE[edge_type])}' AND ({edge_where})
        ORDER BY candidate_score DESC
        LIMIT 20
    """))
    _print_section("Violations", read_rows(out_dir, "violations.parquet", f"""
        SELECT violation_type, severity, current_gap, mean_gap, src_node_id, dst_node_id, description
        FROM read_parquet('{{path}}')
        WHERE {pair_where}
        ORDER BY current_gap DESC, mean_gap DESC
        LIMIT 20
    """))
    _print_section("Conditionals", read_rows(out_dir, "conditional_edges.parquet", f"""
        SELECT a_node_id, b_node_id, method, p_a_given_b, lower_bound, upper_bound, confidence
        FROM read_parquet('{{path}}')
        WHERE {conditional_where}
        ORDER BY confidence DESC, method
        LIMIT 20
    """))


def _edge_where(src: str, dst: str, edge_type: str) -> str:
    forward = f"src_node_id = '{q(src)}' AND dst_node_id = '{q(dst)}'"
    if edge_type == "implies":
        return forward
    reverse = f"src_node_id = '{q(dst)}' AND dst_node_id = '{q(src)}'"
    return f"({forward}) OR ({reverse})"


def _print_section(title: str, rows: list[dict[str, object]]) -> None:
    print(f"\n{title}")
    _print_rows(rows)


def _print_rows(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("No rows.")
        return
    cols = list(rows[0])
    widths = {
        col: min(80, max(len(col), *(len(str(row.get(col, ""))) for row in rows)))
        for col in cols
    }
    print("  ".join(col.ljust(widths[col]) for col in cols))
    print("  ".join("-" * widths[col] for col in cols))
    for row in rows:
        print("  ".join(str(row.get(col, ""))[: widths[col]].ljust(widths[col]) for col in cols))


if __name__ == "__main__":
    raise SystemExit(main())
