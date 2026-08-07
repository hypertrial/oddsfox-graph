#!/usr/bin/env python3
"""Benchmark shared odds-history parquet scan + builders.

Examples:
  uv run python scripts/benchmark_odds_history.py
  uv run python scripts/benchmark_odds_history.py --data-dir data --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.hourly_scan import split_history_source_rows
from oddsgraph.odds_history import build_odds_histories


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    settings.configure_build_dir(args.build_dir.resolve())
    if args.data_dir is not None:
        settings.configure_data_dir(args.data_dir.resolve())
    settings.ensure_dirs()
    input_glob = settings.resolve_input_glob()

    scan_times: list[float] = []
    advance_n = stage_n = 0
    for _ in range(max(1, args.repeats)):
        started = time.perf_counter()
        advance_rows, stage_rows = split_history_source_rows(input_glob)
        scan_times.append(time.perf_counter() - started)
        advance_n = len(advance_rows)
        stage_n = len(stage_rows)

    build_times: list[float] = []
    for _ in range(max(1, args.repeats)):
        started = time.perf_counter()
        build_odds_histories(settings)
        build_times.append(time.perf_counter() - started)

    payload = {
        "input_glob": input_glob,
        "repeats": args.repeats,
        "scan_median_sec": round(_median(scan_times), 4),
        "build_histories_median_sec": round(_median(build_times), 4),
        "advance_rows": advance_n,
        "stage_rows": stage_n,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
