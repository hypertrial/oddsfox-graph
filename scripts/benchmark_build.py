#!/usr/bin/env python3
"""Benchmark deterministic build stages (propositions, rules, resolve, pipeline).

Examples:
  uv run python scripts/benchmark_build.py --markets tests/fixtures/golden_semantic_markets.parquet
  uv run python scripts/benchmark_build.py --repeats 3 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.ontology import NodeType
from oddsgraph.pipeline import build_pipeline_from_markets
from oddsgraph.propositions import compile_propositions
from oddsgraph.reduce import load_semantic_markets
from oddsgraph.resolution import resolve_fragments
from oddsgraph.rules import apply_rules
from oddsgraph.schema import CanonicalNode, SemanticMarket


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _time_stage(repeats: int, fn) -> dict:
    times: list[float] = []
    last = None
    for _ in range(repeats):
        started = time.perf_counter()
        last = fn()
        times.append(time.perf_counter() - started)
    return {
        "repeats": repeats,
        "times_sec": [round(t, 4) for t in times],
        "median_sec": round(_median(times), 4),
        "result": last,
    }


def _outcomes_from_propositions(propositions: dict) -> list[CanonicalNode]:
    return [
        CanonicalNode(
            canonical_id=oid,
            type=NodeType.OUTCOME,
            label=oid,
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["bench"],
            resolution_method="exact_id",
            inference_method="proposition_compiler",
            proposition=prop,
        )
        for oid, prop in propositions.items()
    ]


def _run_benchmark(
    markets: list[SemanticMarket],
    *,
    repeats: int,
    competition_label: str,
) -> dict:
    compile_stats = _time_stage(
        repeats,
        lambda: compile_propositions(markets, competition_label=competition_label),
    )
    compilation = compile_stats.pop("result")

    rule_nodes = _outcomes_from_propositions(compilation.propositions)
    rules_stats = _time_stage(repeats, lambda: apply_rules(rule_nodes))
    rule_edges = rules_stats.pop("result") or []

    det = build_deterministic_fragments_by_event(
        markets,
        include_topology=True,
        competition_label=competition_label,
    )
    fragments = list(det.values())
    if compilation.fragment.nodes or compilation.fragment.edges:
        fragments.append(compilation.fragment)
    methods = ["deterministic"] * len(det)
    if compilation.fragment.nodes or compilation.fragment.edges:
        methods.append("proposition_compiler")

    settings = Settings()
    settings.official_bracket = False
    settings.compile_propositions = False
    settings.apply_rules = False
    resolve_stats = _time_stage(
        repeats,
        lambda: resolve_fragments(fragments, settings, inference_methods=methods),
    )
    resolve_stats.pop("result", None)

    pipeline_settings = Settings()
    pipeline_settings.official_bracket = False
    pipeline_settings.compile_propositions = True
    pipeline_settings.apply_rules = True
    pipeline_stats = _time_stage(
        repeats,
        lambda: build_pipeline_from_markets(pipeline_settings, markets, {}),
    )
    pipeline_result = pipeline_stats.pop("result")

    return {
        "markets": len(markets),
        "propositions": len(compilation.propositions),
        "rule_edges": len(rule_edges),
        "pipeline_nodes": len(pipeline_result.graph.nodes) if pipeline_result else 0,
        "pipeline_edges": len(pipeline_result.graph.edges) if pipeline_result else 0,
        "stages": {
            "compile_propositions": compile_stats,
            "apply_rules": rules_stats,
            "resolve_fragments": resolve_stats,
            "build_pipeline_from_markets": pipeline_stats,
        },
    }


def _markdown_table(report: dict) -> str:
    headers = ["stage", "median_sec", "repeats"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for stage, stats in report["stages"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    stage,
                    str(stats["median_sec"]),
                    str(stats["repeats"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--markets",
        type=Path,
        default=Path("build/semantic_markets.parquet"),
        help="Semantic markets parquet path",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Timed repetitions per stage (median reported)",
    )
    parser.add_argument(
        "--limit-markets",
        type=int,
        default=None,
        help="Optional cap on markets loaded (for quick smoke runs)",
    )
    parser.add_argument(
        "--competition-label",
        default="World Cup 2026",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON report to stdout",
    )
    args = parser.parse_args()

    if not args.markets.exists():
        raise SystemExit(f"Markets parquet not found: {args.markets}")
    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")

    markets = load_semantic_markets(args.markets)
    if args.limit_markets is not None:
        markets = markets[: max(0, args.limit_markets)]

    report = _run_benchmark(
        markets,
        repeats=args.repeats,
        competition_label=args.competition_label,
    )
    report["markets_path"] = str(args.markets)
    report["markdown"] = _markdown_table(report)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {args.output}")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"markets={report['markets']} propositions={report['propositions']} "
            f"rule_edges={report['rule_edges']}"
        )
        print(report["markdown"])


if __name__ == "__main__":
    main()
