from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from oddsfox_graph._discovery.provenance import atomic_write_json, sha256_file
from oddsfox_graph._discovery.performance_contracts import (
    current_hardware,
    current_python_version,
    load_performance_budget,
)
from oddsfox_graph._discovery.versions import (
    CANONICAL_CATALOG_SHA256,
    FAST_READY_BENCHMARK_HARNESS_SHA256,
    FAST_READY_BENCHMARK_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    SOURCE_SCHEMA,
)


_READY_PROBE = """
import sys
from pathlib import Path

from oddsfox_graph.graph import Graph

graph = Graph.open(Path(sys.argv[1]))
print(graph.metadata().model_dump_json())
"""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _load_budget(path: Path, repetitions: int) -> dict[str, Any]:
    budget = load_performance_budget(path)
    hardware = current_hardware()
    if repetitions != budget.repetitions:
        raise ValueError(
            f"Performance budget repetitions mismatch: expected "
            f"{budget.repetitions}, got {repetitions}"
        )
    if hardware.system != budget.system or hardware.machine != budget.machine:
        raise ValueError("Performance budget hardware binding does not match")
    if hardware.processor != budget.processor_exact:
        raise ValueError("Performance budget processor binding does not match")
    if current_python_version() != budget.python_version:
        raise ValueError("Performance budget Python binding does not match")
    harness_digest = sha256_file(Path(__file__).resolve())
    if harness_digest != budget.versions.benchmark_harness_sha256:
        raise ValueError("Performance budget benchmark harness binding does not match")
    return budget.model_dump(mode="json")


def _open_ready_graph(
    out_dir: Path,
    *,
    started: float,
) -> tuple[float, dict[str, Any]]:
    """Probe readiness in a fresh isolated interpreter included in timing."""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", _READY_PROBE, str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ready_seconds = time.monotonic() - started
    if completed.returncode != 0:
        raise RuntimeError(
            "Fresh manifest/query readiness probe failed:\n" + completed.stderr[-4000:]
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Fresh manifest/query readiness probe returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Fresh manifest/query readiness probe returned a non-object")
    return ready_seconds, payload


def _run_once(
    input_path: Path,
    out_dir: Path,
    *,
    deadline_seconds: float,
    repetition: int,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "oddsfox_graph.cli",
            "discover",
            "--mode",
            "fast",
            "--input",
            str(input_path),
            "--input-profile",
            SOURCE_SCHEMA,
            "--out",
            str(out_dir),
            "--deadline-seconds",
            str(deadline_seconds),
            "--progress-format",
            "json",
            "--output-format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Fast repetition {repetition} failed:\n{completed.stderr[-4000:]}"
        )
    manifest_query_ready, metadata = _open_ready_graph(out_dir, started=started)
    manifest = _read_json(out_dir / "build_manifest.json")
    stats = manifest.get("stats")
    if not isinstance(stats, dict):
        raise RuntimeError("Fast manifest has no stats")
    if manifest.get("build_mode") != "fast":
        raise RuntimeError("Benchmark did not produce a fast graph")
    forbidden = (
        "primary_model_manifest.json",
        "verifier_model_manifest.json",
        "automation_profile.json",
        "compute_profile.json",
    )
    if any((out_dir / name).exists() for name in forbidden):
        raise RuntimeError("Fast output unexpectedly contains inference provenance")
    inference_resources = stats.get("inference_resources_loaded")
    if inference_resources != []:
        raise RuntimeError(
            "Fast mode loaded inference resources: " + str(inference_resources)
        )
    return {
        "repetition": repetition,
        "manifest_query_ready_seconds": round(manifest_query_ready, 6),
        "deadline_met": bool(manifest.get("deadline", {}).get("met")),
        "logical_artifact_hashes": manifest.get("artifact_hashes"),
        "peak_rss_mb": stats.get("peak_rss_mb"),
        "stage_metrics": stats.get("stage_metrics"),
        "candidate_edges": stats.get("candidate_edges"),
        "logic_edges": stats.get("logic_edges"),
        "cross_market_edges": stats.get("cross_market_deterministic_edges"),
        "cross_event_edges": stats.get("cross_event_deterministic_edges"),
        "publication_bytes": stats.get("publication_bytes"),
        "inference_resources_loaded": inference_resources,
        "viewer_metadata": metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process-isolated full-catalog fast-mode benchmark."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--deadline-seconds", type=float, default=120.0)
    parser.add_argument("--performance-budget", required=True, type=Path)
    parser.add_argument("--require-gates", action="store_true")
    args = parser.parse_args()

    input_path = args.input.resolve()
    if sha256_file(input_path) != CANONICAL_CATALOG_SHA256:
        raise ValueError("Fast performance validation requires the canonical catalog")
    if args.repetitions != 3:
        raise ValueError("The v0.13 release budget requires exactly three repetitions")
    budget = _load_budget(args.performance_budget.resolve(), args.repetitions)
    harness_digest = sha256_file(Path(__file__).resolve())
    if harness_digest != FAST_READY_BENCHMARK_HARNESS_SHA256:
        raise ValueError("Benchmark harness source does not match its version binding")
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for repetition in range(1, args.repetitions + 1):
        run = _run_once(
            input_path,
            work_dir / f"fast-run-{repetition}",
            deadline_seconds=args.deadline_seconds,
            repetition=repetition,
        )
        runs.append(run)
        print(
            json.dumps(
                {
                    "repetition": repetition,
                    "manifest_query_ready_seconds": run["manifest_query_ready_seconds"],
                    "peak_rss_mb": run["peak_rss_mb"],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
    max_ready = float(budget["gates"]["max_manifest_query_ready_seconds"])
    hashes = [
        json.dumps(run["logical_artifact_hashes"], sort_keys=True) for run in runs
    ]
    acceptance = {
        "every_run_ready_within_budget": all(
            float(run["manifest_query_ready_seconds"]) <= max_ready for run in runs
        ),
        "every_discovery_deadline_met": all(run["deadline_met"] for run in runs),
        "logical_hashes_identical": len(set(hashes)) == 1,
        "inference_resources_absent": all(
            run.get("inference_resources_loaded") == [] for run in runs
        ),
    }
    result = {
        "schema_version": PERFORMANCE_BUDGET_VERSION,
        "benchmark_contract": FAST_READY_BENCHMARK_VERSION,
        "benchmark_harness_sha256": harness_digest,
        "input_sha256": CANONICAL_CATALOG_SHA256,
        "python_version": current_python_version(),
        "hardware": current_hardware().model_dump(mode="json"),
        "budget": budget,
        "runs": runs,
        "acceptance": acceptance,
        "passed": all(acceptance.values()),
    }
    atomic_write_json(args.output.resolve(), result)
    return int(args.require_gates and not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
