from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from oddsfox_graph._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256 as _canonical_json_sha256,
    sha256_file as _sha256,
)
from oddsfox_graph._discovery.versions import (
    CACHE_ENTRY_VERSION,
    CACHE_FILENAME,
    CACHE_FORMAT,
    FAKE_RUNTIME_VERSION,
    MODEL_PROFILE_SCHEMA_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    RELEASE_FIXTURE_SCHEMA_VERSION,
)
from oddsfox_graph import __version__

CANONICAL_SOURCE_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)
CANONICAL_MARKET_ROWS = 94_781
REQUIRED_SOURCE_COLUMNS = {
    "market_id",
    "question",
    "outcomes",
    "clob_token_ids",
}
FIXTURE_SCHEMA_VERSION = RELEASE_FIXTURE_SCHEMA_VERSION
PACKAGE_VERSION = __version__
REQUIRED_FILES = (
    "input.parquet",
    "benchmark.parquet",
    "performance-report.json",
    "compute-profile.json",
    "model-manifest.json",
    "model-profile.json",
    "calibration-report.json",
    "expected-artifact-hashes.json",
    "baselines/5000/build_manifest.json",
    "baselines/20000/build_manifest.json",
)
REQUIRED_TREES = (
    "cache",
    "baselines/5000",
    "baselines/20000",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PERFORMANCE_SIZES = (5_000, 20_000)
_PERFORMANCE_MODES = (
    "clean",
    "offline",
    "one-market-incremental",
)
_PERFORMANCE_GATES = {
    "5000:clean_wall_seconds",
    "5000:incremental_candidate_faster",
    "5000:offline_candidate_reuse",
    "20000:clean_peak_rss_mb",
    "20000:incremental_candidate_faster",
    "20000:offline_candidate_reuse",
    "20000:publication_seconds",
}
_PERFORMANCE_BUDGET_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "m4-v0.8-performance-budget.json"
)

def _validate_canonical_catalog(path: Path) -> None:
    if _sha256(path) != CANONICAL_SOURCE_SHA256:
        raise ValueError("Release validation requires the canonical supplied catalog")
    from oddsfox_graph.queries import DuckDB, q

    db = DuckDB()
    try:
        rows = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(path)}')") or 0
        )
        columns = {
            str(row["name"])
            for row in db.rows(
                f"SELECT name FROM parquet_schema('{q(path)}') "
                "WHERE name != 'duckdb_schema'"
            )
        }
    finally:
        db.close()
    missing = sorted(REQUIRED_SOURCE_COLUMNS - columns)
    if rows != CANONICAL_MARKET_ROWS or missing:
        raise ValueError(
            "Canonical catalog contract mismatch: "
            f"expected {CANONICAL_MARKET_ROWS} rows and "
            f"{sorted(REQUIRED_SOURCE_COLUMNS)}; got {rows} rows"
            + (f" with missing columns {missing}" if missing else "")
        )


def _tree_provenance(path: Path) -> dict[str, Any]:
    entries = sorted(path.rglob("*"))
    symlinks = [item for item in entries if item.is_symlink()]
    if symlinks:
        raise ValueError(
            "Release fixture trees cannot contain symbolic links: "
            + str(symlinks[0])
        )
    files = [item for item in entries if item.is_file()]
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "file_count": len(files)}


def _fixture_path(fixture_root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.as_posix() != relative
    ):
        raise ValueError(f"Unsafe release fixture path: {relative}")
    return fixture_root / relative_path


def _validate_fixture_manifest(
    fixture_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("Unsupported release fixture manifest schema")
    if manifest.get("package_version") != PACKAGE_VERSION:
        raise ValueError("Release fixture package version is incompatible")
    if manifest.get("source_sha256") != CANONICAL_SOURCE_SHA256:
        raise ValueError("Release fixture source binding is not canonical")
    files = manifest.get("files")
    trees = manifest.get("trees")
    if not isinstance(files, dict) or not set(REQUIRED_FILES) <= set(files):
        raise ValueError("Release fixture manifest is missing required file bindings")
    if not isinstance(trees, dict) or not set(REQUIRED_TREES) <= set(trees):
        raise ValueError("Release fixture manifest is missing required tree bindings")
    for relative, expected in sorted(files.items()):
        path = _fixture_path(fixture_root, str(relative))
        if (
            not isinstance(expected, str)
            or not _SHA256_PATTERN.fullmatch(expected)
            or not path.is_file()
            or path.is_symlink()
            or _sha256(path) != expected
        ):
            raise ValueError(f"Release fixture file provenance mismatch: {relative}")
    for relative, expected in sorted(trees.items()):
        path = _fixture_path(fixture_root, str(relative))
        if (
            not isinstance(expected, dict)
            or set(expected) != {"sha256", "file_count"}
            or not isinstance(expected.get("sha256"), str)
            or not _SHA256_PATTERN.fullmatch(expected["sha256"])
            or not isinstance(expected.get("file_count"), int)
            or isinstance(expected.get("file_count"), bool)
            or expected["file_count"] < 1
            or not path.is_dir()
            or _tree_provenance(path) != expected
        ):
            raise ValueError(f"Release fixture tree provenance mismatch: {relative}")
    return manifest


def _validate_fixture_argument_bindings(
    args: argparse.Namespace,
    fixture_root: Path,
    manifest: dict[str, Any],
    limits: list[int],
) -> None:
    file_arguments = {
        "input.parquet": args.input,
        "benchmark.parquet": args.benchmark,
        "performance-report.json": args.performance_report,
        "compute-profile.json": args.compute_profile,
        "model-manifest.json": args.model_manifest,
        "model-profile.json": args.model_profile,
        "calibration-report.json": args.calibration_report,
        "expected-artifact-hashes.json": args.expected_hashes,
    }
    for relative, path in file_arguments.items():
        fixture_path = fixture_root / relative
        if not path.is_file() or (
            path.resolve() != fixture_path.resolve()
            and _sha256(path) != manifest["files"][relative]
        ):
            raise ValueError(
                f"Release validation argument is not fixture-bound: {relative}"
            )
    fixture_cache = fixture_root / "cache"
    if not args.cache_dir.is_dir() or (
        args.cache_dir.resolve() != fixture_cache.resolve()
        and _tree_provenance(args.cache_dir) != manifest["trees"]["cache"]
    ):
        raise ValueError("Release validation cache is not fixture-bound")
    for limit in limits:
        relative = f"baselines/{limit}"
        if relative not in manifest["trees"]:
            raise ValueError(
                f"Release fixture does not bind baseline limit {limit}"
            )
        baseline = args.baseline_dir / str(limit)
        fixture_baseline = fixture_root / relative
        if (
            not baseline.is_dir()
            or (
                baseline.resolve() != fixture_baseline.resolve()
                and _tree_provenance(baseline) != manifest["trees"][relative]
            )
        ):
            raise ValueError(
                f"Release validation baseline is not fixture-bound: {limit}"
            )


def _baseline_requested_models(
    manifest: dict[str, Any],
) -> tuple[str, str]:
    models = manifest.get("models")
    if not isinstance(models, dict):
        raise ValueError("Release baseline has no model provenance")
    requested: list[str] = []
    for role in ("parse", "classify"):
        role_data = models.get(role)
        value = (
            role_data.get("requested")
            if isinstance(role_data, dict)
            else None
        )
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Release baseline has no requested {role} model"
            )
        requested.append(value)
    return requested[0], requested[1]


def _validate_performance_report(path: Path) -> None:
    performance = json.loads(path.read_text(encoding="utf-8"))
    performance_budget = performance.get("performance_budget")
    expected_budget = json.loads(
        _PERFORMANCE_BUDGET_PATH.read_text(encoding="utf-8")
    )
    performance_hardware = performance.get("hardware")
    acceptance = performance.get("acceptance")
    if (
        performance.get("fake_runtime_version") != FAKE_RUNTIME_VERSION
        or performance.get("repetitions") != 3
        or performance.get("sizes") != list(_PERFORMANCE_SIZES)
        or set(performance.get("modes") or [])
        != set(_PERFORMANCE_MODES)
        or not isinstance(performance_hardware, dict)
        or performance_hardware.get("system") != "Darwin"
        or performance_hardware.get("machine") != "arm64"
        or performance_hardware.get("processor") != "Apple M4"
        or not isinstance(performance_budget, dict)
        or performance_budget != expected_budget
        or expected_budget.get("schema_version") != PERFORMANCE_BUDGET_VERSION
        or expected_budget.get("input_sha256") != CANONICAL_SOURCE_SHA256
        or not isinstance(acceptance, dict)
        or acceptance.get("passed") is not True
    ):
        raise ValueError("Release performance report did not pass the bound M4 gates")
    expected_samples = {
        (repetition, size, mode)
        for repetition in range(1, 4)
        for size in _PERFORMANCE_SIZES
        for mode in _PERFORMANCE_MODES
    }
    samples = performance.get("samples")
    if not isinstance(samples, list) or len(samples) != len(expected_samples):
        raise ValueError("Release performance report has incomplete samples")
    observed_samples: set[tuple[int, int, str]] = set()
    clean_hashes: dict[tuple[int, int], dict[str, str]] = {}
    offline_hashes: dict[tuple[int, int], dict[str, str]] = {}
    samples_by_mode: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Release performance report has malformed samples")
        repetition = sample.get("repetition")
        size = sample.get("size")
        mode = sample.get("mode")
        artifact_hashes = sample.get("artifact_hashes")
        if (
            not isinstance(repetition, int)
            or isinstance(repetition, bool)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(mode, str)
            or not isinstance(artifact_hashes, dict)
            or not artifact_hashes
            or any(
                not isinstance(name, str)
                or not isinstance(digest, str)
                or not _SHA256_PATTERN.fullmatch(digest)
                for name, digest in artifact_hashes.items()
            )
        ):
            raise ValueError("Release performance report has malformed samples")
        sample_id = (repetition, size, mode)
        if sample_id in observed_samples:
            raise ValueError("Release performance report has duplicate samples")
        observed_samples.add(sample_id)
        samples_by_mode.setdefault((size, mode), []).append(sample)
        keyed_hashes = {
            str(name): str(digest)
            for name, digest in artifact_hashes.items()
        }
        if mode == "clean":
            clean_hashes[(repetition, size)] = keyed_hashes
        elif mode == "offline":
            offline_hashes[(repetition, size)] = keyed_hashes
        else:
            equivalent_hashes = sample.get("equivalent_full_artifact_hashes")
            if equivalent_hashes != artifact_hashes:
                raise ValueError(
                    "Release performance report does not prove incremental "
                    "and clean-build equality"
                )
    if (
        observed_samples != expected_samples
        or clean_hashes != offline_hashes
    ):
        raise ValueError(
            "Release performance report does not prove complete deterministic replay"
        )

    def median_metric(
        size: int,
        mode: str,
        *path_parts: str,
    ) -> float:
        values: list[float] = []
        for sample in samples_by_mode.get((size, mode), []):
            value: object = sample
            for part in path_parts:
                if not isinstance(value, dict) or part not in value:
                    raise ValueError(
                        "Release performance report has incomplete measurements"
                    )
                value = value[part]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(
                    "Release performance report has invalid measurements"
                )
            values.append(float(value))
        if len(values) != 3:
            raise ValueError(
                "Release performance report has incomplete measurements"
            )
        return float(statistics.median(values))

    limits = expected_budget["gates"]
    clean_candidate_seconds = {
        size: median_metric(
            size,
            "clean",
            "stage_timings",
            "generate_candidates",
        )
        for size in _PERFORMANCE_SIZES
    }
    if any(value <= 0.0 for value in clean_candidate_seconds.values()):
        raise ValueError(
            "Release performance report has invalid candidate timings"
        )
    measured_gates = {
        "5000:clean_wall_seconds": (
            median_metric(5_000, "clean", "wall_seconds")
            <= float(limits["max_5000_clean_wall_seconds"])
        ),
        "20000:clean_peak_rss_mb": (
            median_metric(20_000, "clean", "peak_rss_mb")
            <= float(limits["max_20000_clean_peak_rss_mb"])
        ),
        "20000:publication_seconds": (
            median_metric(
                20_000,
                "clean",
                "stage_timings",
                "publish_artifacts",
            )
            <= float(limits["max_20000_publication_seconds"])
        ),
    }
    for size in _PERFORMANCE_SIZES:
        measured_gates[f"{size}:offline_candidate_reuse"] = (
            median_metric(
                size,
                "offline",
                "stage_timings",
                "generate_candidates",
            )
            / clean_candidate_seconds[size]
            <= 0.25
        )
        measured_gates[f"{size}:incremental_candidate_faster"] = (
            median_metric(
                size,
                "one-market-incremental",
                "stage_timings",
                "generate_candidates",
            )
            / clean_candidate_seconds[size]
            <= 0.95
        )
    if set(measured_gates) != _PERFORMANCE_GATES or not all(
        measured_gates.values()
    ):
        raise ValueError(
            "Release performance report measurements did not pass the bound M4 gates"
        )
    summary = performance.get("summary")
    expected_summary = {
        f"{size}:{mode}"
        for size in _PERFORMANCE_SIZES
        for mode in _PERFORMANCE_MODES
    }
    if (
        not isinstance(summary, dict)
        or set(summary) != expected_summary
        or any(
            not isinstance(value, dict) or value.get("runs") != 3
            for value in summary.values()
        )
    ):
        raise ValueError("Release performance report has incomplete summaries")
    gates = acceptance.get("gates")
    comparisons = acceptance.get("performance_comparison")
    if (
        not isinstance(gates, dict)
        or set(gates) != _PERFORMANCE_GATES
        or gates != measured_gates
        or not isinstance(comparisons, dict)
        or set(comparisons) != _PERFORMANCE_GATES
        or any(
            not isinstance(value, dict) or value.get("passed") is not True
            for value in comparisons.values()
        )
    ):
        raise ValueError("Release performance report has incomplete gate results")


def _validate_content_bindings(
    args: argparse.Namespace,
    limits: list[int],
) -> None:
    model_manifest = json.loads(args.model_manifest.read_text(encoding="utf-8"))
    model_profile = json.loads(args.model_profile.read_text(encoding="utf-8"))
    calibration = json.loads(args.calibration_report.read_text(encoding="utf-8"))
    compute = json.loads(args.compute_profile.read_text(encoding="utf-8"))
    _validate_performance_report(args.performance_report)
    if model_manifest.get("license") != "Apache-2.0":
        raise ValueError("Release model manifest must use the approved Apache-2.0 license")
    if model_manifest.get("runtime") not in {"llama.cpp", "vllm"}:
        raise ValueError("Release model manifest has an unsupported runtime")
    if (
        model_profile.get("schema_version") != MODEL_PROFILE_SCHEMA_VERSION
        or set(model_profile.get("request_contract_hashes") or {})
        != {"parse", "classify"}
    ):
        raise ValueError("Release model profile has incompatible request contracts")
    if (
        model_profile.get("model_manifest_id") != model_manifest.get("manifest_id")
        or model_profile.get("model_manifest_sha256")
        != _canonical_json_sha256(model_manifest)
    ):
        raise ValueError("Release model profile is not bound to the model manifest")
    if (
        model_profile.get("benchmark_sha256") != _sha256(args.benchmark)
        or calibration.get("profile_id") != model_profile.get("profile_id")
    ):
        raise ValueError("Calibration/profile/benchmark bindings do not match")
    if (
        calibration.get("passed") is not True
        or float(calibration.get("structured_output_validity") or 0.0)
        < 0.999
        or "nli" not in (model_profile.get("inference_fingerprints") or {})
    ):
        raise ValueError("Release calibration did not pass profile gates")
    if (
        not isinstance(compute.get("hardware_hour_usd"), (int, float))
        or isinstance(compute.get("hardware_hour_usd"), bool)
    ):
        raise ValueError("Compute profile is missing hardware_hour_usd")
    cache_path = args.cache_dir / CACHE_FILENAME
    if not cache_path.is_file():
        raise ValueError("Release cache has incompatible inference lineage")
    allowed_cache_files = {
        CACHE_FILENAME,
        f"{CACHE_FILENAME}-wal",
        f"{CACHE_FILENAME}-shm",
    }
    if any(
        item.name not in allowed_cache_files
        for item in args.cache_dir.iterdir()
    ):
        raise ValueError("Release cache has incompatible inference lineage")
    cache_wal = args.cache_dir / f"{CACHE_FILENAME}-wal"
    if cache_wal.is_file() and cache_wal.stat().st_size:
        raise ValueError("Release cache must be checkpointed before validation")
    cache_db = sqlite3.connect(f"file:{cache_path.as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(
            cache_db.execute(
                "SELECT key, value FROM cache_metadata"
            ).fetchall()
        )
        integrity = cache_db.execute("PRAGMA integrity_check").fetchone()
        incompatible = int(
            cache_db.execute(
                "SELECT count(*) FROM cache_entries WHERE entry_version != ?",
                [CACHE_ENTRY_VERSION],
            ).fetchone()[0]
        )
    except sqlite3.DatabaseError as exc:
        raise ValueError(
            "Release cache has incompatible inference lineage"
        ) from exc
    finally:
        cache_db.close()
    if (
        metadata
        != {
            "format": CACHE_FORMAT,
            "entry_version": str(CACHE_ENTRY_VERSION),
            "lineage": "self-hosted-open-model",
        }
        or integrity is None
        or integrity[0] != "ok"
        or incompatible
    ):
        raise ValueError("Release cache has incompatible inference lineage")
    for limit in limits:
        manifest = json.loads(
            (
                args.baseline_dir / str(limit) / "build_manifest.json"
            ).read_text(encoding="utf-8")
        )
        inference = manifest.get("inference") or {}
        if (
            manifest.get("version") != PACKAGE_VERSION
            or inference.get("model_manifest_id") != model_manifest.get("manifest_id")
            or inference.get("model_manifest_hash")
            != _canonical_json_sha256(model_manifest)
            or inference.get("model_profile_id") != model_profile.get("profile_id")
            or inference.get("proprietary_cache_lineage") is not False
        ):
            raise ValueError(
                f"Baseline {limit} is not bound to the current open-model fixture"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate cache-complete discovery against the real catalog."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--expected-hashes", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--performance-report", required=True, type=Path)
    parser.add_argument("--compute-profile", required=True, type=Path)
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--model-profile", required=True, type=Path)
    parser.add_argument("--calibration-report", required=True, type=Path)
    parser.add_argument("--fixture-manifest", required=True, type=Path)
    parser.add_argument("--propositions", default="5000,20000")
    args = parser.parse_args()
    _validate_canonical_catalog(args.input)
    fixture_root = args.input.resolve().parent
    fixture_manifest = _validate_fixture_manifest(
        fixture_root,
        args.fixture_manifest.resolve(),
    )

    from oddsfox_graph.discovery import DiscoveryConfig, discover

    expected = json.loads(args.expected_hashes.read_text(encoding="utf-8"))
    limits = [int(value) for value in args.propositions.split(",") if value]
    if not limits or any(value < 1 for value in limits):
        raise ValueError("--propositions must contain positive comma-separated limits")
    _validate_fixture_argument_bindings(
        args,
        fixture_root,
        fixture_manifest,
        limits,
    )
    _validate_content_bindings(args, limits)

    results: dict[str, Any] = {}
    results["fixture"] = fixture_manifest
    results["performance"] = json.loads(
        args.performance_report.read_text(encoding="utf-8")
    )
    for limit in limits:
        baseline_manifest = json.loads(
            (
                args.baseline_dir / str(limit) / "build_manifest.json"
            ).read_text(encoding="utf-8")
        )
        parse_model, classify_model = _baseline_requested_models(
            baseline_manifest
        )
        out = args.work_dir / str(limit)
        stats = discover(
            args.input,
            out,
            config=DiscoveryConfig(
                cache_dir=args.cache_dir,
                benchmark_path=args.benchmark,
                incremental_from=args.baseline_dir / str(limit),
                compute_profile=args.compute_profile,
                model_manifest=args.model_manifest,
                model_profile=args.model_profile,
                require_ready=True,
                offline=True,
                parse_model=parse_model,
                classify_model=classify_model,
                max_propositions=limit,
            ),
        )
        manifest = json.loads(
            (out / "build_manifest.json").read_text(encoding="utf-8")
        )
        expected_for_limit = expected.get(str(limit))
        if expected_for_limit is None:
            raise ValueError(f"Expected hashes do not contain limit {limit}")
        if manifest["artifact_hashes"] != expected_for_limit:
            raise RuntimeError(
                f"Artifact hashes for {limit} propositions do not match the "
                "recorded online run"
            )
        results[str(limit)] = {
            "stats": stats,
            "artifact_hashes": manifest["artifact_hashes"],
            "stage_timings": manifest["stage_timings"],
            "cache": manifest["cache"],
            "usage": manifest["usage"],
        }

    largest = max(limits)
    results["evaluation"] = json.loads(
        (
            args.work_dir / str(largest) / "evaluation_report.json"
        ).read_text(encoding="utf-8")
    )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.work_dir / "release-validation.json", results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
