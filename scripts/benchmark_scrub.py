#!/usr/bin/env python3
"""Benchmark explorer scrub projection (stage-odds lookup + frame cache).

Examples:
  uv run python scripts/benchmark_scrub.py
  uv run python scripts/benchmark_scrub.py --build-dir build --repeats 20 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from oddsgraph.bracket_projection import apply_bracket_projection, latest_reach_prob
from oddsgraph.config import Settings
from oddsgraph.explorer.canvas_actions import (
    _project_elements,
    projected_frame_cache_stats,
    reset_projected_frame_cache,
)
from oddsgraph.explorer.data import bracket_elements, odds_time_bounds, stage_odds_by_team


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _time_ms(repeats: int, fn) -> dict:
    times: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        times.append((time.perf_counter() - started) * 1000.0)
    return {
        "repeats": repeats,
        "times_ms": [round(t, 4) for t in times],
        "median_ms": round(_median(times), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--repeats", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    settings.configure_build_dir(args.build_dir.resolve())
    if not settings.nodes_path.exists():
        raise SystemExit(f"Missing nodes parquet at {settings.nodes_path}")

    min_hour, max_hour = odds_time_bounds(settings)
    if min_hour is None or max_hour is None:
        raise SystemExit("Odds time bounds unavailable; run odds-history first")

    stage_odds = stage_odds_by_team(settings)
    series_lens = [
        len(series)
        for team_map in stage_odds.values()
        for series in team_map.values()
    ]
    longest = max(
        (
            series
            for team_map in stage_odds.values()
            for series in team_map.values()
        ),
        key=len,
        default=[],
    )
    mid = int(longest[len(longest) // 2]["h"]) if longest else int(min_hour)

    elements = bracket_elements(settings).to_elements()
    project_stats = _time_ms(
        args.repeats,
        lambda: apply_bracket_projection(elements, mid, stage_odds),
    )
    lookup_stats = _time_ms(
        args.repeats * 10,
        lambda: latest_reach_prob(longest, mid) if longest else None,
    )

    reset_projected_frame_cache()
    _project_elements(settings, int(min_hour))
    first = projected_frame_cache_stats()["project_count"]
    _project_elements(
        settings,
        int(max_hour),
        previous_hour_epoch=int(min_hour),
    )
    second = projected_frame_cache_stats()["project_count"]

    payload = {
        "build_dir": str(args.build_dir),
        "stage_series_count": len(series_lens),
        "stage_series_avg_len": round(sum(series_lens) / len(series_lens), 1)
        if series_lens
        else 0.0,
        "stage_series_max_len": max(series_lens) if series_lens else 0,
        "project_median_ms": project_stats["median_ms"],
        "longest_lookup_median_ms": lookup_stats["median_ms"],
        "frame_cache_projects_first": first,
        "frame_cache_projects_after_scrub": second,
        "frame_cache_extra_projects_on_scrub": second - first,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
