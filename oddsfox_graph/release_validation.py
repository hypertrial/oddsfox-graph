"""Installed, content-bound v0.11 fast-mode release validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from . import __version__
from ._discovery.provenance import atomic_write_json, canonical_json_sha256, sha256_file
from ._discovery.versions import (
    CANONICAL_CATALOG_SHA256,
    EXTRACTOR_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    RELEASE_FIXTURE_SCHEMA_VERSION,
    RULE_VERSION,
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
    manifest = _read_json(baseline_root / "build_manifest.json")
    if manifest.get("version") != __version__:
        raise ValueError("The fast baseline version is incompatible")
    if manifest.get("build_mode") != "fast":
        raise ValueError("The release baseline is not a fast build")
    if manifest.get("validation_status") != "DETERMINISTIC_VALIDATED":
        raise ValueError("The fast baseline is not deterministic validated")
    manifest_input = manifest.get("input")
    if (
        not isinstance(manifest_input, dict)
        or manifest_input.get("sha256") != CANONICAL_CATALOG_SHA256
    ):
        raise ValueError("The fast baseline does not bind the canonical input")

    expected_document = _read_json(root / "expected_artifact_hashes.json")
    expected_hashes = expected_document.get("fast", expected_document)
    if not isinstance(expected_hashes, dict) or expected_hashes != manifest.get(
        "artifact_hashes"
    ):
        raise ValueError("The fast baseline artifact hashes do not match")

    coverage = _read_json(baseline_root / "coverage_summary.json")
    selection = coverage.get("input_selection")
    if (
        coverage.get("all_market_selection") is not True
        or int(coverage.get("markets") or 0) != 94_777
        or int(coverage.get("propositions") or 0) != 189_570
        or not isinstance(selection, dict)
        or int(selection.get("input_market_rows") or 0) != 94_781
        or int(selection.get("invalid_market_rows") or 0) != 4
    ):
        raise ValueError("The fast baseline catalog counts are not canonical")

    stats = manifest.get("stats")
    if not isinstance(stats, dict):
        raise ValueError("The fast baseline has no stats")
    expected_counts = {
        "same_market_complement_edges": 94_771,
        "same_market_categorical_exclusion_edges": 54,
    }
    for field, expected_count in expected_counts.items():
        if int(stats.get(field) or 0) != expected_count:
            raise ValueError(f"The fast baseline has an invalid {field}")
    if int(stats.get("cross_market_deterministic_edges") or 0) <= 0:
        raise ValueError("The fast baseline has no cross-market deterministic edges")
    if int(stats.get("cross_event_deterministic_edges") or 0) <= 0:
        raise ValueError("The fast baseline has no cross-event deterministic edges")
    deadline = manifest.get("deadline")
    if not isinstance(deadline, dict) or deadline.get("met") is not True:
        raise ValueError("The fast baseline missed its discovery deadline")

    viewer = _read_json(baseline_root / "viewer_manifest.json")
    if (
        viewer.get("build_mode") != "fast"
        or viewer.get("validation_status") != "DETERMINISTIC_VALIDATED"
        or not viewer.get("graph_content_fingerprint")
    ):
        raise ValueError("The fast viewer manifest is incomplete")

    performance = _read_json(root / "performance_report.json")
    if performance.get("passed") is not True:
        raise ValueError("Release performance gates did not pass")
    hardware = performance.get("hardware")
    budget = performance.get("budget")
    acceptance = performance.get("acceptance")
    if (
        performance.get("schema_version") != PERFORMANCE_BUDGET_VERSION
        or performance.get("input_sha256") != CANONICAL_CATALOG_SHA256
        or not isinstance(hardware, dict)
        or hardware.get("system") != "Darwin"
        or hardware.get("machine") != "arm64"
        or "Apple M4" not in str(hardware.get("processor") or "")
        or not isinstance(budget, dict)
        or budget.get("schema_version") != PERFORMANCE_BUDGET_VERSION
        or budget.get("input_sha256") != CANONICAL_CATALOG_SHA256
        or budget.get("repetitions") != 3
        or budget.get("selection") != "complete-valid-catalog"
        or budget.get("extractor_version") != EXTRACTOR_VERSION
        or budget.get("rule_version") != RULE_VERSION
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
        raise ValueError("Release performance report is not bound to the v0.11 M4 budget")
    runs = performance.get("runs")
    if not isinstance(runs, list) or len(runs) != 3:
        raise ValueError("Release performance requires three isolated runs")
    logical_hashes: list[object] = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("Release performance run is invalid")
        ready_seconds = float(run.get("time_to_ready_seconds") or float("inf"))
        if ready_seconds > 120 or run.get("deadline_met") is not True:
            raise ValueError("A fast performance repetition missed 120 seconds")
        run_hashes = run.get("logical_artifact_hashes")
        if run_hashes != expected_hashes:
            raise ValueError(
                "A fast performance repetition does not match the release baseline"
            )
        logical_hashes.append(run_hashes)
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


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"Unsafe release fixture path: {relative}")
    return candidate


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
