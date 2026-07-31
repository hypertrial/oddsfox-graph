from __future__ import annotations

import json
from pathlib import Path
from typing import Any


COUNT_KEYS = (
    "input_rows",
    "markets",
    "tokens",
    "candidate_edges",
    "classified_pairs",
    "logic_edges",
    "conditional_edges",
    "review_queue",
)


def load_manifest(out_dir: Path) -> dict[str, Any]:
    value = json.loads(
        (out_dir / "build_manifest.json").read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise ValueError("build_manifest.json must contain a JSON object")
    return {str(key): item for key, item in value.items()}


def benchmark_summary(out_dir: Path, *, top_stages: int = 8) -> str:
    manifest = load_manifest(out_dir)
    record = _manifest_record(out_dir, manifest, top_stages=top_stages)
    stats = record["stats"]

    lines = [
        f"build: {out_dir}",
        f"runtime_seconds: {stats.get('runtime_seconds')}",
        "counts:",
    ]
    for key in COUNT_KEYS:
        if key in stats:
            lines.append(f"  {key}: {stats[key]}")
    lines.append(f"artifacts: {record['artifact_count']}")
    lines.append("top_stage_timings:")
    for name, seconds in record["top_stage_timings"].items():
        lines.append(f"  {name}: {seconds}s")
    return "\n".join(lines) + "\n"


def _manifest_record(
    out_dir: Path,
    manifest: dict[str, Any],
    *,
    top_stages: int = 8,
) -> dict[str, Any]:
    timings = manifest.get("stage_timings") or {}
    ranked = sorted(
        ((str(name), float(seconds)) for name, seconds in timings.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:top_stages]
    return {
        "out_dir": str(out_dir),
        "stats": manifest.get("stats") or {},
        "artifact_count": len(manifest.get("artifacts") or []),
        "top_stage_timings": {name: seconds for name, seconds in ranked},
    }
