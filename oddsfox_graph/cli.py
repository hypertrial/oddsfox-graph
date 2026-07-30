from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .benchmark import benchmark_summary
from .build import build
from .search import read_rows, resolve_node, search_nodes


EDGE_TYPES = ("complement", "equivalent", "implies", "mutually_exclusive")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oddsfox-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--taxonomy", type=Path, default=None)

    p = sub.add_parser("benchmark-summary")
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("nodes")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--top", type=int, default=50)

    p = sub.add_parser("edges")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--edge-type", default=None, choices=EDGE_TYPES)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "build":
            stats = build(args.input, args.out, taxonomy_path=args.taxonomy)
            for key, value in stats.items():
                print(f"{key}: {value}")
        elif args.cmd == "benchmark-summary":
            print(benchmark_summary(args.out), end="")
        elif args.cmd == "nodes":
            _print_rows(
                read_rows(
                    args.out,
                    "nodes.parquet",
                    f"""
                    SELECT node_id, market_id, outcome_label, event_slug, canonical_proposition
                    FROM read_parquet('{{path}}')
                    ORDER BY event_slug, market_id, outcome_index
                    LIMIT {int(args.top)}
                    """,
                )
            )
        elif args.cmd == "edges":
            edge_filter = "WHERE edge_type = ?" if args.edge_type else ""
            params = [args.edge_type] if args.edge_type else None
            _print_rows(
                read_rows(
                    args.out,
                    "logic_edges.parquet",
                    f"""
                    SELECT edge_type, edge_basis, confidence, src_node_id, dst_node_id, evidence
                    FROM read_parquet('{{path}}')
                    {edge_filter}
                    ORDER BY edge_basis, edge_type, src_node_id, dst_node_id
                    LIMIT {int(args.top)}
                    """,
                    params,
                )
            )
        elif args.cmd == "condition":
            a = resolve_node(args.out, args.a, require_unique=True)
            b = resolve_node(args.out, args.b, require_unique=True)
            if not a or not b:
                raise ValueError("Could not resolve both nodes")
            _print_rows(
                read_rows(
                    args.out,
                    "conditional_edges.parquet",
                    """
                    SELECT *
                    FROM read_parquet('{path}')
                    WHERE a_node_id = ? AND b_node_id = ?
                    LIMIT 20
                    """,
                    [a, b],
                )
            )
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
    _print_section(
        "Node",
        read_rows(
            out_dir,
            "nodes.parquet",
            """
            SELECT
                node_id,
                market_id,
                outcome_label,
                event_slug,
                market_family,
                canonical_proposition
            FROM read_parquet('{path}')
            WHERE node_id = ?
            """,
            [node],
        ),
    )
    _print_section(
        "Same-Market Nodes",
        read_rows(
            out_dir,
            "nodes.parquet",
            f"""
            SELECT
                n.node_id AS sibling_node_id,
                n.outcome_label,
                n.canonical_proposition
            FROM read_parquet('{{path}}') n
            WHERE n.market_id = (
                SELECT market_id FROM read_parquet('{{path}}') WHERE node_id = ?
            )
                AND n.node_id != ?
            ORDER BY n.outcome_index
            """,
            [node, node],
        ),
    )
    _print_section("Logic Edges", _touching_edges(out_dir, node))
    _print_section(
        "Conditionals",
        read_rows(
            out_dir,
            "conditional_edges.parquet",
            """
            SELECT a_node_id, b_node_id, method, p_a_given_b, confidence, evidence
            FROM read_parquet('{path}')
            WHERE a_node_id = ? OR b_node_id = ?
            ORDER BY method, a_node_id, b_node_id
            LIMIT 20
            """,
            [node, node],
        ),
    )


def _touching_edges(out_dir: Path, node: str) -> list[dict[str, object]]:
    return read_rows(
        out_dir,
        "logic_edges.parquet",
        """
        SELECT edge_type, edge_basis, confidence, src_node_id, dst_node_id, evidence
        FROM read_parquet('{path}')
        WHERE src_node_id = ? OR dst_node_id = ?
        ORDER BY edge_basis, edge_type
        LIMIT 20
        """,
        [node, node],
    )


def _print_explain_edge(out_dir: Path, src: str, dst: str, edge_type: str) -> None:
    edge_where, edge_params = _edge_where(src, dst, edge_type)
    conditional_where = (
        "(a_node_id = ? AND b_node_id = ?) OR (a_node_id = ? AND b_node_id = ?)"
    )
    _print_section(
        "Logic Edge",
        read_rows(
            out_dir,
            "logic_edges.parquet",
            f"""
            SELECT edge_type, edge_basis, confidence, src_node_id, dst_node_id, evidence
            FROM read_parquet('{{path}}')
            WHERE edge_type = ? AND ({edge_where})
            ORDER BY confidence DESC
            LIMIT 20
            """,
            [edge_type, *edge_params],
        ),
    )
    _print_section(
        "Conditionals",
        read_rows(
            out_dir,
            "conditional_edges.parquet",
            f"""
            SELECT a_node_id, b_node_id, method, p_a_given_b, confidence, evidence
            FROM read_parquet('{{path}}')
            WHERE {conditional_where}
            ORDER BY method
            LIMIT 20
            """,
            [src, dst, dst, src],
        ),
    )


def _edge_where(src: str, dst: str, edge_type: str) -> tuple[str, list[str]]:
    forward = "src_node_id = ? AND dst_node_id = ?"
    if edge_type == "implies":
        return forward, [src, dst]
    return f"({forward}) OR ({forward})", [src, dst, dst, src]


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
