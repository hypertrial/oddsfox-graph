#!/usr/bin/env python3
"""Benchmark local LLM backends for oddsgraph structured extraction.

Examples:
  uv run python scripts/benchmark_infer.py --backends inprocess --limit 1
  uv run python scripts/benchmark_infer.py --backends server --concurrency 1,2
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM, build_graph_llm
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


def _prepare_prompt(
    settings: Settings,
    event_id: str,
    markets: list[SemanticMarket],
) -> tuple[str, int, int]:
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
    return prompt, estimate_prompt_tokens(prompt), len(chunk)


def _time_generate(
    llm: BaseGraphLLM,
    prompt: str,
    event_id: str,
) -> dict:
    started = time.perf_counter()
    fragment = llm.generate_fragment(prompt, event_id, max_tokens_override=2048)
    elapsed = time.perf_counter() - started
    out_tokens = max(1, len(fragment.model_dump_json()) // 4)
    return {
        "event_id": event_id,
        "output_tokens_est": out_tokens,
        "elapsed_sec": round(elapsed, 3),
        "tok_per_sec_est": round(out_tokens / elapsed, 2) if elapsed > 0 else None,
        "nodes": len(fragment.nodes),
        "edges": len(fragment.edges),
    }


def _run_trial(
    settings: Settings,
    event_id: str,
    markets: list[SemanticMarket],
    llm: BaseGraphLLM | None = None,
) -> dict:
    prompt, prompt_tokens, markets_in_chunk = _prepare_prompt(
        settings, event_id, markets
    )
    llm = llm or build_graph_llm(settings)
    result = _time_generate(llm, prompt, event_id)
    result.update(
        {
            "markets_in_chunk": markets_in_chunk,
            "prompt_tokens_est": prompt_tokens,
        }
    )
    return result


def _run_concurrent_server_trial(
    settings: Settings,
    events: list[tuple[str, list[SemanticMarket]]],
) -> dict:
    """Issue concurrent generate_fragment calls against one shared server LLM."""
    if settings.llm_backend != "server":
        raise ValueError("Concurrent trials require --backends server")
    concurrency = max(1, settings.llm_concurrency)
    llm = build_graph_llm(settings)
    prepared = [
        (event_id, *_prepare_prompt(settings, event_id, markets))
        for event_id, markets in events
    ]
    # Warm one request so connection/setup is outside the timed window.
    warm_event, warm_prompt, _, _ = prepared[0]
    _time_generate(llm, warm_prompt, warm_event)

    started = time.perf_counter()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_time_generate, llm, prompt, event_id): event_id
            for event_id, prompt, _, _ in prepared[:concurrency]
        }
        for future in as_completed(futures):
            results.append(future.result())
    wall = time.perf_counter() - started
    total_out = sum(r["output_tokens_est"] for r in results)
    return {
        "mode": "concurrent",
        "concurrency": concurrency,
        "events": [r["event_id"] for r in results],
        "wall_sec": round(wall, 3),
        "sum_elapsed_sec": round(sum(r["elapsed_sec"] for r in results), 3),
        "output_tokens_est": total_out,
        "tok_per_sec_est": round(total_out / wall, 2) if wall > 0 else None,
        "per_event": results,
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
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        if row.get("error"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("backend", "")),
                        str(row.get("n_ctx", "")),
                        str(row.get("concurrency", "")),
                        str(row.get("event_id", "")),
                        "ERR",
                        "",
                        "",
                    ]
                )
                + " |"
            )
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("backend", "")),
                    str(row.get("n_ctx", "")),
                    str(row.get("concurrency", "")),
                    str(row.get("event_id", row.get("mode", ""))),
                    str(row.get("elapsed_sec", row.get("wall_sec", ""))),
                    str(row.get("tok_per_sec_est", "")),
                    str(row.get("nodes", "")),
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
        help="Comma-separated concurrency values (server only; >1 runs concurrent trial)",
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
                if backend != "server" and concurrency != 1:
                    continue
                settings = Settings()
                settings.llm_backend = backend
                settings.n_ctx = n_ctx
                settings.llm_concurrency = concurrency
                settings.resume = False
                settings.use_few_shot_exemplars = False
                print(
                    f"bench backend={backend} n_ctx={n_ctx} concurrency={concurrency} ..."
                )
                try:
                    if backend == "server" and concurrency > 1:
                        # Need at least `concurrency` events; repeat if necessary.
                        expanded = list(events)
                        while len(expanded) < concurrency:
                            expanded.extend(events)
                        result = _run_concurrent_server_trial(
                            settings, expanded[:concurrency]
                        )
                        result.update(
                            {
                                "backend": backend,
                                "n_ctx": n_ctx,
                                "event_id": f"concurrentx{concurrency}",
                            }
                        )
                        rows.append(result)
                        print(
                            f"  wall={result['wall_sec']}s "
                            f"tok/s≈{result['tok_per_sec_est']} "
                            f"sum_elapsed={result['sum_elapsed_sec']}s"
                        )
                    else:
                        llm = build_graph_llm(settings)
                        for event_id, event_markets in events:
                            result = _run_trial(
                                settings, event_id, event_markets, llm=llm
                            )
                            result.update(
                                {
                                    "backend": backend,
                                    "n_ctx": n_ctx,
                                    "concurrency": concurrency,
                                }
                            )
                            rows.append(result)
                            print(
                                f"  event={event_id} elapsed={result['elapsed_sec']}s "
                                f"tok/s≈{result['tok_per_sec_est']} "
                                f"nodes={result['nodes']}"
                            )
                except Exception as exc:
                    rows.append(
                        {
                            "backend": backend,
                            "n_ctx": n_ctx,
                            "concurrency": concurrency,
                            "event_id": "",
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
