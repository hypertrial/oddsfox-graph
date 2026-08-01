from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from oddsfox_graph._discovery.provenance import atomic_write_json, sha256_file
from oddsfox_graph._discovery.versions import (
    CANONICAL_CATALOG_SHA256,
    EXTRACTOR_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    RULE_VERSION,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _hardware() -> dict[str, str]:
    processor = platform.processor()
    if platform.system() == "Darwin":
        observed = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            check=False,
            text=True,
        ).stdout.strip()
        processor = observed or processor
        if "Apple" not in processor:
            profile = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True,
                check=False,
                text=True,
            ).stdout
            processor = next(
                (
                    line.partition(":")[2].strip()
                    for line in profile.splitlines()
                    if line.strip().startswith("Chip:")
                ),
                "",
            )
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": processor,
    }


def _load_budget(path: Path, repetitions: int) -> dict[str, Any]:
    budget = _read_json(path)
    hardware = _hardware()
    bindings = {
        "schema_version": PERFORMANCE_BUDGET_VERSION,
        "input_sha256": CANONICAL_CATALOG_SHA256,
        "system": hardware["system"],
        "machine": hardware["machine"],
        "repetitions": repetitions,
        "extractor_version": EXTRACTOR_VERSION,
        "rule_version": RULE_VERSION,
    }
    for key, expected in bindings.items():
        if budget.get(key) != expected:
            raise ValueError(
                f"Performance budget {key} mismatch: expected {expected!r}, "
                f"got {budget.get(key)!r}"
            )
    processor_contains = str(budget.get("processor_contains") or "")
    if processor_contains and processor_contains not in hardware["processor"]:
        raise ValueError("Performance budget processor binding does not match")
    return budget


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_meta(out_dir: Path, *, started: float) -> tuple[float, dict[str, Any]]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "oddsfox_graph.cli",
            "serve",
            "--out",
            str(out_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        url = f"http://127.0.0.1:{port}/api/v1/meta"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Explorer server exited before metadata was ready")
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    payload = json.loads(response.read())
                    if response.status == 200 and isinstance(payload, dict):
                        return time.monotonic() - started, payload
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                time.sleep(0.05)
        raise RuntimeError("Explorer metadata endpoint was not ready within 15 seconds")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


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
    time_to_ready, metadata = _wait_for_meta(out_dir, started=started)
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
        "time_to_ready_seconds": round(time_to_ready, 6),
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
        raise ValueError("The v0.11 release budget requires exactly three repetitions")
    budget = _load_budget(args.performance_budget.resolve(), args.repetitions)
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
                    "time_to_ready_seconds": run["time_to_ready_seconds"],
                    "peak_rss_mb": run["peak_rss_mb"],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
    max_ready = float(budget["gates"]["max_time_to_ready_seconds"])
    hashes = [json.dumps(run["logical_artifact_hashes"], sort_keys=True) for run in runs]
    acceptance = {
        "every_run_ready_within_budget": all(
            float(run["time_to_ready_seconds"]) <= max_ready for run in runs
        ),
        "every_discovery_deadline_met": all(run["deadline_met"] for run in runs),
        "logical_hashes_identical": len(set(hashes)) == 1,
        "inference_resources_absent": all(
            run.get("inference_resources_loaded") == [] for run in runs
        ),
    }
    result = {
        "schema_version": PERFORMANCE_BUDGET_VERSION,
        "input_sha256": CANONICAL_CATALOG_SHA256,
        "hardware": _hardware(),
        "budget": budget,
        "runs": runs,
        "acceptance": acceptance,
        "passed": all(acceptance.values()),
    }
    atomic_write_json(args.output.resolve(), result)
    return int(args.require_gates and not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
