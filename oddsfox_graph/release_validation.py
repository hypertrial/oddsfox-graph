"""Installed, content-bound v0.13 fast-mode release validation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from . import __version__
from ._discovery.performance_contracts import (
    HardwareBinding,
    load_performance_budget,
    validate_performance_budget,
)
from ._discovery.manifest_contracts import (
    CoverageSummary,
    FastBuildManifest,
    load_build_manifest,
    load_viewer_manifest,
    validate_manifest_pair,
)
from ._discovery.provenance import atomic_write_json, canonical_json_sha256, sha256_file
from ._discovery.versions import (
    CANONICAL_CATALOG_SHA256,
    FAST_READY_BENCHMARK_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    RELEASE_FIXTURE_SCHEMA_VERSION,
    discovery_semantics_fingerprint,
)


REQUIRED_FILES = (
    "input.parquet",
    "performance_report.json",
    "expected_artifact_hashes.json",
    "baselines/fast/build_manifest.json",
    "baselines/fast/viewer_manifest.json",
    "baselines/fast/coverage_summary.json",
)
REQUIRED_TREES = ("baselines/fast",)


def validate_release_fixture(fixture_root: Path, work_dir: Path) -> dict[str, Any]:
    """Validate the deterministic fast release without requiring model assets."""

    root = fixture_root.resolve()
    raw = _read_json(root / "release-fixture.json")
    if raw.get("schema_version") != RELEASE_FIXTURE_SCHEMA_VERSION:
        raise ValueError("Unsupported release fixture schema")
    if raw.get("package_version") != __version__:
        raise ValueError("Release fixture package version does not match")
    if raw.get("source_sha256") != CANONICAL_CATALOG_SHA256:
        raise ValueError("Release fixture source hash is not canonical")

    files = _string_mapping(raw.get("files"), "files")
    for relative in files:
        _safe_path(root, relative)
    missing_files = sorted(set(REQUIRED_FILES) - set(files))
    if missing_files:
        raise ValueError(
            "Release fixture is missing file bindings: " + ", ".join(missing_files)
        )
    checked_files: dict[str, str] = {}
    for relative, expected in sorted(files.items()):
        candidate = _safe_path(root, relative)
        if not candidate.is_file():
            raise ValueError(f"Release fixture file is missing: {relative}")
        observed = sha256_file(candidate)
        if observed != expected:
            raise ValueError(f"Release fixture hash mismatch: {relative}")
        checked_files[relative] = observed
    if checked_files["input.parquet"] != CANONICAL_CATALOG_SHA256:
        raise ValueError("Release fixture input is not the canonical catalog")

    trees = raw.get("trees")
    if not isinstance(trees, dict):
        raise ValueError("Release fixture trees must be an object")
    checked_trees: dict[str, dict[str, object]] = {}
    for relative in REQUIRED_TREES:
        binding = trees.get(relative)
        if not isinstance(binding, dict):
            raise ValueError(f"Release fixture does not bind tree {relative}")
        observed_hash, observed_count = _tree_digest(_safe_path(root, relative))
        if (
            observed_hash != binding.get("sha256")
            or observed_count != binding.get("file_count")
        ):
            raise ValueError(f"Release fixture tree hash mismatch: {relative}")
        checked_trees[relative] = {
            "sha256": observed_hash,
            "file_count": observed_count,
        }

    baseline_root = root / "baselines" / "fast"
    try:
        manifest = load_build_manifest(baseline_root / "build_manifest.json")
        viewer = load_viewer_manifest(baseline_root / "viewer_manifest.json")
        coverage = CoverageSummary.model_validate_json(
            (baseline_root / "coverage_summary.json").read_bytes()
        )
        validate_manifest_pair(manifest, viewer)
    except (OSError, ValueError) as exc:
        raise ValueError("The fast baseline contracts are incompatible") from exc
    if not isinstance(manifest, FastBuildManifest):
        raise ValueError("The release baseline is not a fast build")
    if manifest.version != __version__:
        raise ValueError("The fast baseline version is incompatible")
    if (
        manifest.discovery_semantics_fingerprint
        != discovery_semantics_fingerprint()
    ):
        raise ValueError("The fast baseline discovery semantics are incompatible")
    if manifest.validation_status != "DETERMINISTIC_VALIDATED":
        raise ValueError("The fast baseline is not deterministic validated")
    if manifest.input.sha256 != CANONICAL_CATALOG_SHA256:
        raise ValueError("The fast baseline does not bind the canonical input")

    _verify_manifest_file_hashes(baseline_root, manifest)

    expected_document = _read_json(root / "expected_artifact_hashes.json")
    expected_hashes = expected_document.get("fast", expected_document)
    if not isinstance(expected_hashes, dict) or expected_hashes != (
        manifest.artifact_hashes
    ):
        raise ValueError("The fast baseline artifact hashes do not match")

    if (
        coverage.all_market_selection is not True
        or coverage.markets != 94_777
        or coverage.propositions != 189_570
        or coverage.input_selection.input_market_rows != 94_781
        or coverage.input_selection.invalid_market_rows != 4
    ):
        raise ValueError("The fast baseline catalog counts are not canonical")

    stats = manifest.stats
    expected_counts = {
        "same_market_complement_edges": 94_771,
        "same_market_categorical_exclusion_edges": 54,
    }
    for field, expected_count in expected_counts.items():
        if _integer_stat(stats, field) != expected_count:
            raise ValueError(f"The fast baseline has an invalid {field}")
    if _integer_stat(stats, "cross_market_deterministic_edges") <= 0:
        raise ValueError("The fast baseline has no cross-market deterministic edges")
    if _integer_stat(stats, "cross_event_deterministic_edges") != 0:
        raise ValueError(
            "The fast baseline contains unsupported cross-event deterministic edges"
        )
    if manifest.deadline.met is not True:
        raise ValueError("The fast baseline missed its discovery deadline")

    if viewer.validation_status != "DETERMINISTIC_VALIDATED":
        raise ValueError("The fast viewer manifest is incomplete")

    performance = _read_json(root / "performance_report.json")
    if performance.get("passed") is not True:
        raise ValueError("Release performance gates did not pass")
    hardware = performance.get("hardware")
    acceptance = performance.get("acceptance")
    try:
        report_budget = validate_performance_budget(performance.get("budget"))
        packaged_budget = load_performance_budget()
        observed_hardware = HardwareBinding.model_validate(hardware)
    except ValueError as exc:
        raise ValueError(
            "Release performance report is not bound to the packaged M4 budget"
        ) from exc
    if (
        performance.get("schema_version") != PERFORMANCE_BUDGET_VERSION
        or performance.get("benchmark_contract")
        != FAST_READY_BENCHMARK_VERSION
        or performance.get("benchmark_harness_sha256")
        != report_budget.versions.benchmark_harness_sha256
        or performance.get("python_version") != report_budget.python_version
        or performance.get("input_sha256") != CANONICAL_CATALOG_SHA256
        or report_budget != packaged_budget
        or observed_hardware.system != report_budget.system
        or observed_hardware.machine != report_budget.machine
        or observed_hardware.processor != report_budget.processor_exact
        or not isinstance(acceptance, dict)
        or not all(
            acceptance.get(name) is True
            for name in (
                "every_run_ready_within_budget",
                "every_discovery_deadline_met",
                "logical_hashes_identical",
                "inference_resources_absent",
            )
        )
    ):
        raise ValueError("Release performance report is not bound to the packaged M4 budget")
    max_ready_seconds = report_budget.gates.max_manifest_query_ready_seconds
    runs = performance.get("runs")
    if not isinstance(runs, list) or len(runs) != 3:
        raise ValueError("Release performance requires three isolated runs")
    logical_hashes: list[object] = []
    repetitions: set[int] = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("Release performance run is invalid")
        repetition = run.get("repetition")
        if not isinstance(repetition, int) or isinstance(repetition, bool):
            raise ValueError("Release performance run repetition is invalid")
        repetitions.add(repetition)
        ready_value = run.get("manifest_query_ready_seconds")
        if not isinstance(ready_value, (int, float)) or isinstance(ready_value, bool):
            raise ValueError("Release performance run ready time is invalid")
        ready_seconds = float(ready_value)
        if (
            not math.isfinite(ready_seconds)
            or ready_seconds < 0
            or ready_seconds > max_ready_seconds
            or run.get("deadline_met") is not True
        ):
            raise ValueError(
                "A fast performance repetition missed the ready-time budget"
            )
        if run.get("inference_resources_loaded") != []:
            raise ValueError(
                "A fast performance repetition loaded inference resources"
            )
        run_hashes = run.get("logical_artifact_hashes")
        if run_hashes != expected_hashes:
            raise ValueError(
                "A fast performance repetition does not match the release baseline"
            )
        logical_hashes.append(run_hashes)
    if repetitions != {1, 2, 3}:
        raise ValueError("Release performance repetitions must be exactly 1, 2, and 3")
    if any(item is None for item in logical_hashes) or len(
        {canonical_json_sha256(item) for item in logical_hashes}
    ) != 1:
        raise ValueError("Fast performance repetitions changed logical hashes")

    result = {
        "passed": True,
        "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
        "checked_files": checked_files,
        "checked_trees": checked_trees,
        "decision": "DETERMINISTIC_VALIDATED",
    }
    target = work_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / "release-validation.json", result)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid release fixture JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Release fixture JSON must be an object: {path}")
    return cast(dict[str, Any], value)


def _string_mapping(value: object, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Release fixture {name} must be a nonempty object")
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError(f"Release fixture {name} must map strings to strings")
    return cast(dict[str, str], value)


def _integer_stat(stats: Mapping[str, object], name: str) -> int:
    value = stats.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"The fast baseline has an invalid {name}")
    return value


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"Unsafe release fixture path: {relative}")
    return candidate


def _verify_manifest_file_hashes(
    baseline_root: Path,
    manifest: FastBuildManifest,
) -> None:
    observed: dict[str, str] = {}
    for group_name, bindings in (
        ("artifact", manifest.artifact_hashes),
        ("state", manifest.state_hashes),
        ("published file", manifest.published_file_hashes),
    ):
        for relative, expected in sorted(bindings.items()):
            candidate = _safe_path(baseline_root, relative)
            if not candidate.is_file():
                raise ValueError(
                    f"The fast baseline {group_name} is missing: {relative}"
                )
            digest = observed.get(relative)
            if digest is None:
                digest = sha256_file(candidate)
                observed[relative] = digest
            if digest != expected:
                raise ValueError(
                    f"The fast baseline {group_name} hash mismatch: {relative}"
                )


def _tree_digest(directory: Path) -> tuple[str, int]:
    if not directory.is_dir():
        raise ValueError(f"Release fixture tree is missing: {directory}")
    rows = [
        {"path": path.relative_to(directory).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]
    if not rows:
        raise ValueError(f"Release fixture tree is empty: {directory}")
    return canonical_json_sha256(rows), len(rows)
