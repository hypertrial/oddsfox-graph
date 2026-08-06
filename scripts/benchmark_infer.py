#!/usr/bin/env python3
"""Benchmark local LLM backends for oddsgraph structured extraction.

Examples:
  uv run python scripts/benchmark_infer.py --backends inprocess --limit 1
  uv run python scripts/benchmark_infer.py --backends inprocess,server --concurrency 1,2
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.llm import build_graph_llm
from oddsgraph.prompts import build_event_prompt, chunk_markets_for_prompt, estimate_prompt_tokens
from oddsgraph.reduce import load_semantic_markets
from oddsgraph.schema import SemanticMarket


def _pick_events(
    markets: list[SemanticMarket],
    limit: int,
    event_ids: list[str] | None = None,
) -> list[tuple[str, list[SemanticMarket]]]:
    by_event: dict[str, list[SemanticMarket]] = {}
    for market in markets:
        by_event.setdefault(market.event_id, []).append(market)
    if event_ids:
        selected = []
        for eid in event_ids:
            if eid in by_event:
                selected.append((eid, by_event[eid]))
        return selected[:limit]
    # Prefer modest-sized events (enough signal, not huge prompts).
    ordered = sorted(by_event.items(), key=lambda item: abs(len(item[1]) - 4))
    return ordered[:limit]


def _run_trial(
    settings: Settings,
    event_id: str,
    markets: list[SemanticMarket],
) -> dict:
    chunks = chunk_markets_for_prompt(
        markets,
        event_id,
        settings.chunk_token_budget,
        settings.chunk_output_token_budget,
        settings.max_markets_per_chunk,
        settings.max_text_field_chars,
        settings.n_ctx,
        settings.chunk_context_safety_margin,
    )
    chunk = chunks[0]
    prompt = build_event_prompt(event_id, chunk, settings.max_text_field_chars)
    prompt_tokens = estimate_prompt_tokens(prompt)
    llm = build_graph_llm(settings)
    started = time.perf_counter()
    fragment = llm.generate_fragment(prompt, event_id, max_tokens_override=2048)
    elapsed = time.perf_counter() - started
    # Approximate output tokens from compact JSON length.
    out_tokens = max(1, len(fragment.model_dump_json()) // 4)
    return {
        "event_id": event_id,
        "markets_in_chunk": len(chunk),
        "prompt_tokens_est": prompt_tokens,
        "output_tokens_est": out_tokens,
        "elapsed_sec": round(elapsed, 3),
        "tok_per_sec_est": round(out_tokens / elapsed, 2) if elapsed > 0 else None,
        "nodes": len(fragment.nodes),
        "edges": len(fragment.edges),
    }


def _markdown_table(rows: list[dict]) -> str:
    headers = [
        "backend",
        "n_ctx",
        "concurrency",
        "event_id",
        "elapsed_sec",
        "tok_per_sec_est",
        "nodes",
        "edges",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(str(row.get(h, "")) for h in headers)
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
    parser.add_argument("--limit", type=int, default=1, help="Events to sample")
    parser.add_argument(
        "--event-id",
        action="append",
        default=[],
        help="Specific event id(s) to benchmark (repeatable)",
    )
    parser.add_argument(
        "--backends",
        default="inprocess",
        help="Comma-separated backends: inprocess,server,mlx",
    )
    parser.add_argument(
        "--concurrency",
        default="1",
        help="Comma-separated concurrency values (server only)",
    )
    parser.add_argument(
        "--n-ctx",
        default="4096,8192",
        help="Comma-separated n_ctx values",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/benchmark_report.json"),
    )
    args = parser.parse_args()

    if not args.markets.exists():
        raise SystemExit(f"Markets parquet not found: {args.markets}")

    markets = load_semantic_markets(args.markets)
    events = _pick_events(markets, args.limit, event_ids=args.event_id or None)
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    concurrencies = [int(x) for x in args.concurrency.split(",") if x.strip()]
    n_ctx_values = [int(x) for x in args.n_ctx.split(",") if x.strip()]

    rows: list[dict] = []
    for backend in backends:
        for n_ctx in n_ctx_values:
            for concurrency in concurrencies:
                if backend != "server" and concurrency != concurrencies[0]:
                    continue
                settings = Settings()
                settings.llm_backend = backend
                settings.n_ctx = n_ctx
                settings.llm_concurrency = concurrency
                settings.resume = False
                settings.use_few_shot_exemplars = False
                for event_id, event_markets in events:
                    print(
                        f"bench backend={backend} n_ctx={n_ctx} "
                        f"concurrency={concurrency} event={event_id} ..."
                    )
                    try:
                        result = _run_trial(settings, event_id, event_markets)
                        result.update(
                            {
                                "backend": backend,
                                "n_ctx": n_ctx,
                                "concurrency": concurrency,
                            }
                        )
                        rows.append(result)
                        print(
                            f"  elapsed={result['elapsed_sec']}s "
                            f"tok/s≈{result['tok_per_sec_est']} "
                            f"nodes={result['nodes']}"
                        )
                    except Exception as exc:
                        rows.append(
                            {
                                "backend": backend,
                                "n_ctx": n_ctx,
                                "concurrency": concurrency,
                                "event_id": event_id,
                                "error": str(exc),
                            }
                        )
                        print(f"  FAILED: {exc}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"trials": rows, "markdown": _markdown_table(rows)}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n" + report["markdown"])
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
