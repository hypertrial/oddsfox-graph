from __future__ import annotations

import json
from pathlib import Path
from typing import Any


COUNT_KEYS = (
    "input_rows",
    "markets",
    "tokens",
    "candidate_edges",
    "logic_edges",
    "price_edges",
    "derived_edges",
    "violations",
    "incoherent_events",
)


def benchmark_summary(out_dir: Path, *, top_stages: int = 8) -> str:
    manifest_path = out_dir / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stats: dict[str, Any] = manifest.get("stats") or {}
    options: dict[str, Any] = manifest.get("build_options") or {}
    timings: dict[str, Any] = manifest.get("stage_timings") or {}
    artifacts = manifest.get("artifacts") or []

    lines = [
        f"build: {out_dir}",
        f"runtime_seconds: {stats.get('runtime_seconds')}",
        f"history_mode: {stats.get('history_mode', 'unknown')}",
        "build_options: " + _format_options(options),
        "counts:",
    ]
    for key in COUNT_KEYS:
        if key in stats:
            lines.append(f"  {key}: {stats[key]}")
    lines.append(f"artifacts: {len(artifacts)}")
    lines.append("top_stage_timings:")
    for name, seconds in sorted(timings.items(), key=lambda item: item[1], reverse=True)[:top_stages]:
        lines.append(f"  {name}: {seconds}s")
    return "\n".join(lines) + "\n"


def _format_options(options: dict[str, Any]) -> str:
    if not options:
        return "{}"
    return " ".join(f"{key}={options[key]}" for key in sorted(options))
