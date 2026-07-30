from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from oddsfox_graph._discovery.contracts import (
    PairClassification,
    PairClassificationBatch,
    ParsedMarket,
    ParsedMarketBatch,
    ParsedOutcome,
)
from oddsfox_graph._discovery.input import load_source_markets
from oddsfox_graph.discovery import DiscoveryConfig, discover
from oddsfox_graph.queries import DuckDB, q


class _Usage:
    input_tokens = 20
    output_tokens = 10
    total_tokens = 30


class _Response:
    def __init__(self, parsed: object) -> None:
        self.output_parsed = parsed
        self.model = "benchmark-fake-model"
        self.usage = _Usage()


class _Responses:
    async def parse(self, **kwargs: object) -> _Response:
        messages = kwargs["input"]
        payload = json.loads(messages[1]["content"])  # type: ignore[index]
        if kwargs["text_format"] is ParsedMarketBatch:
            return _Response(
                ParsedMarketBatch(
                    markets=[
                        ParsedMarket(
                            market_id=str(market["market_id"]),
                            propositions=[
                                ParsedOutcome(
                                    outcome=str(outcome["outcome"]),
                                    subject=[str(market["question"])],
                                    predicate="resolve",
                                    object=None,
                                    operator=None,
                                    threshold=None,
                                    unit=None,
                                    time_start=None,
                                    time_end=None,
                                    competition=None,
                                    event_scope=market.get("event_slug"),
                                    jurisdiction=None,
                                    polarity=(
                                        "negative"
                                        if str(outcome["outcome"]).casefold() == "no"
                                        else "positive"
                                    ),
                                    parse_confidence=0.99,
                                )
                                for outcome in market["outcomes"]
                            ],
                        )
                        for market in payload
                    ]
                )
            )
        return _Response(
            PairClassificationBatch(
                pairs=[
                    PairClassification(
                        pair_id=str(pair["pair_id"]),
                        relation="unrelated",
                        confidence=0.99,
                        supporting_fields=[],
                        explanation="The supplied propositions are unrelated.",
                        assumptions=[],
                        a_implies_b={
                            "supported": False,
                            "supporting_fields": [],
                            "assumptions": [],
                        },
                        b_implies_a={
                            "supported": False,
                            "supporting_fields": [],
                            "assumptions": [],
                        },
                        requires_review=False,
                    )
                    for pair in payload
                ]
            )
        )


class _Client:
    def __init__(self) -> None:
        self.responses = _Responses()


def _embeddings(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
    return np.asarray(
        [
            [
                int.from_bytes(
                    hashlib.sha256(text.encode("utf-8")).digest()[offset : offset + 4],
                    "big",
                )
                / 2**32
                for offset in (0, 4, 8, 12, 16, 20, 24, 28)
            ]
            for text in texts
        ],
        dtype=np.float32,
    )


def _worker(args: argparse.Namespace) -> int:
    config = DiscoveryConfig(
        cache_dir=args.cache_dir,
        incremental_from=args.incremental_from,
        offline=args.offline,
        top_k=args.top_k,
        max_propositions=args.size,
        max_candidates=args.max_candidates,
        max_llm_pairs=args.max_llm_pairs,
    )
    started = time.perf_counter()
    stats = discover(
        args.input,
        args.out,
        config=config,
        _client=_Client(),
        _embedder=_embeddings,
    )
    elapsed = time.perf_counter() - started
    manifest = json.loads(
        (args.out / "build_manifest.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {
                "wall_seconds": round(elapsed, 6),
                "peak_rss_mb": stats["peak_rss_mb"],
                "tokens": stats["tokens"],
                "candidate_edges": stats["candidate_edges"],
                "stage_timings": manifest["stage_timings"],
                "incremental": stats["incremental"],
                "artifact_hashes": manifest["artifact_hashes"],
            },
            sort_keys=True,
        )
    )
    return 0


def _run_worker(
    *,
    input_path: Path,
    out: Path,
    cache_dir: Path,
    size: int,
    args: argparse.Namespace,
    incremental_from: Path | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--input",
        str(input_path),
        "--out",
        str(out),
        "--cache-dir",
        str(cache_dir),
        "--size",
        str(size),
        "--top-k",
        str(args.top_k),
        "--max-candidates",
        str(args.max_candidates),
        "--max-llm-pairs",
        str(args.max_llm_pairs),
    ]
    if incremental_from is not None:
        command.extend(["--incremental-from", str(incremental_from)])
    if offline:
        command.append("--offline")
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _changed_catalog(input_path: Path, output_path: Path) -> str:
    _, _, markets, _ = load_source_markets(input_path, max_propositions=500)
    market_id = markets[0].market_id
    escaped = market_id.replace("'", "''")
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT * REPLACE (
                    CASE
                        WHEN CAST(market_id AS VARCHAR) = '{escaped}'
                            THEN coalesce(description, '')
                                 || ' Benchmark incremental change.'
                        ELSE description
                    END AS description
                )
                FROM read_parquet('{q(input_path)}')
            ) TO '{q(output_path)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    return market_id


def _summaries(samples: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(
            (int(sample["size"]), str(sample["mode"])),
            [],
        ).append(sample)
    return {
        f"{size}:{mode}": {
            "runs": len(rows),
            "median_wall_seconds": statistics.median(
                float(row["wall_seconds"]) for row in rows
            ),
            "median_peak_rss_mb": statistics.median(
                float(row["peak_rss_mb"]) for row in rows
            ),
            "median_candidate_seconds": statistics.median(
                float(row["stage_timings"]["generate_candidates"]) for row in rows
            ),
            "median_publication_seconds": statistics.median(
                float(row["stage_timings"]["publish_artifacts"]) for row in rows
            ),
        }
        for (size, mode), rows in sorted(grouped.items())
    }


def _acceptance(
    summary: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    gates: dict[str, bool] = {}
    ratios: dict[str, float] = {}
    sizes = sorted(
        {
            int(key.split(":", 1)[0])
            for key in summary
        }
    )
    for size in sizes:
        clean = summary.get(f"{size}:clean")
        incremental = summary.get(f"{size}:one-market-incremental")
        offline = summary.get(f"{size}:offline")
        if clean and incremental and size >= 5_000:
            ratio = (
                float(incremental["median_candidate_seconds"])
                / float(clean["median_candidate_seconds"])
            )
            ratios[f"{size}:incremental_candidate_ratio"] = ratio
            gates[f"{size}:incremental_candidate_faster"] = ratio <= 0.95
        if clean and offline:
            ratio = (
                float(offline["median_candidate_seconds"])
                / float(clean["median_candidate_seconds"])
            )
            ratios[f"{size}:offline_candidate_ratio"] = ratio
            gates[f"{size}:offline_candidate_reuse"] = ratio <= 0.25
        prior = (baseline or {}).get(f"{size}:clean")
        if clean and prior:
            runtime_ratio = (
                float(clean["median_wall_seconds"])
                / float(prior["median_wall_seconds"])
            )
            rss_ratio = (
                float(clean["median_peak_rss_mb"])
                / float(prior["median_peak_rss_mb"])
            )
            ratios[f"{size}:clean_runtime_ratio"] = runtime_ratio
            ratios[f"{size}:clean_rss_ratio"] = rss_ratio
            gates[f"{size}:no_runtime_regression"] = runtime_ratio <= 1.10
            gates[f"{size}:no_rss_regression"] = rss_ratio <= 1.10
    return {
        "passed": bool(gates) and all(gates.values()),
        "gates": gates,
        "ratios": ratios,
    }


def _collect_samples(
    args: argparse.Namespace,
    *,
    input_path: Path,
    runs_root: Path,
    sizes: list[int],
    modes: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    changed_input = runs_root / "one-market-changed.parquet"
    changed_market_id = _changed_catalog(input_path, changed_input)
    samples: list[dict[str, Any]] = []
    for repetition in range(1, args.repetitions + 1):
        for size in sizes:
            run_root = runs_root / f"r{repetition}-{size}"
            cache = run_root / "cache"
            clean_out = run_root / "clean"
            clean = _run_worker(
                input_path=input_path,
                out=clean_out,
                cache_dir=cache,
                size=size,
                args=args,
            )
            clean.update(
                {"mode": "clean", "size": size, "repetition": repetition}
            )
            if "clean" in modes:
                samples.append(clean)
            if "offline" in modes:
                offline = _run_worker(
                    input_path=input_path,
                    out=run_root / "offline",
                    cache_dir=cache,
                    size=size,
                    args=args,
                    incremental_from=clean_out,
                    offline=True,
                )
                if offline["artifact_hashes"] != clean["artifact_hashes"]:
                    raise RuntimeError(
                        "Offline replay logical hashes differ from clean"
                    )
                offline.update(
                    {"mode": "offline", "size": size, "repetition": repetition}
                )
                samples.append(offline)
            if "one-market-incremental" in modes:
                incremental = _run_worker(
                    input_path=changed_input,
                    out=run_root / "incremental",
                    cache_dir=cache,
                    size=size,
                    args=args,
                    incremental_from=clean_out,
                )
                changed_clean = _run_worker(
                    input_path=changed_input,
                    out=run_root / "changed-clean",
                    cache_dir=run_root / "changed-clean-cache",
                    size=size,
                    args=args,
                )
                if incremental["artifact_hashes"] != changed_clean["artifact_hashes"]:
                    raise RuntimeError(
                        "One-market incremental logical hashes differ from clean"
                    )
                incremental.update(
                    {
                        "mode": "one-market-incremental",
                        "size": size,
                        "repetition": repetition,
                    }
                )
                samples.append(incremental)
    return changed_market_id, samples


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process-isolated real-catalog discovery benchmark."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sizes", default="500,2000,5000,20000")
    parser.add_argument(
        "--modes",
        default="clean,offline,one-market-incremental",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-candidates", type=int, default=400_000)
    parser.add_argument("--max-llm-pairs", type=int, default=5_000)
    parser.add_argument(
        "--baseline-json",
        type=Path,
        help="Prior harness JSON used for clean runtime/RSS ratio gates.",
    )
    parser.add_argument("--require-pass", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--size", type=int)
    parser.add_argument("--incremental-from", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.worker:
        return _worker(args)
    if args.output is None:
        raise ValueError("--output is required")
    sizes = [int(value) for value in args.sizes.split(",") if value]
    modes = [value for value in args.modes.split(",") if value]
    supported = {"clean", "offline", "one-market-incremental"}
    if not sizes or args.repetitions < 1 or set(modes) - supported:
        raise ValueError("Invalid sizes, modes, or repetitions")
    root = args.output.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{root.stem}-runs-",
        dir=root.parent,
    ) as temporary_runs:
        changed_market_id, samples = _collect_samples(
            args,
            input_path=args.input.resolve(),
            runs_root=Path(temporary_runs),
            sizes=sizes,
            modes=modes,
        )
    summary = _summaries(samples)
    configuration = {
        "top_k": args.top_k,
        "max_candidates": args.max_candidates,
        "max_llm_pairs": args.max_llm_pairs,
    }
    baseline_summary = None
    if args.baseline_json is not None:
        baseline_payload = json.loads(
            args.baseline_json.read_text(encoding="utf-8")
        )
        if baseline_payload.get("configuration") != configuration:
            raise ValueError(
                "Performance baseline configuration does not match this run"
            )
        baseline_summary = baseline_payload.get("summary")
    result = {
        "input": str(args.input.resolve()),
        "changed_market_id": changed_market_id,
        "sizes": sizes,
        "modes": modes,
        "repetitions": args.repetitions,
        "configuration": configuration,
        "samples": samples,
        "summary": summary,
        "acceptance": _acceptance(summary, baseline=baseline_summary),
    }
    root.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return int(args.require_pass and not result["acceptance"]["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
