"""Installed, content-bound v0.10 release fixture validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from . import __version__
from ._discovery.cache import InferenceCache
from ._discovery.inference import (
    load_automation_profile,
    load_compute_profile,
    load_model_manifest,
    manifest_sha256,
    validate_consensus_model_pair,
)
from ._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
)
from ._discovery.versions import RELEASE_FIXTURE_SCHEMA_VERSION
from ._discovery.versions import CANONICAL_CATALOG_SHA256
from .qualification import (
    QUALIFICATION_CASE_COLUMNS,
    qualification_case_set_hash,
)
from .queries import DuckDB, q


REQUIRED_FILES = (
    "input.parquet",
    "performance_report.json",
    "compute_profile.json",
    "primary_model_manifest.json",
    "verifier_model_manifest.json",
    "automation_profile.json",
    "qualification_report.json",
    "qualification_cases.parquet",
    "expected_artifact_hashes.json",
    "baselines/5000/build_manifest.json",
    "baselines/20000/build_manifest.json",
    "baselines/all/build_manifest.json",
    "baselines/all/viewer_manifest.json",
    "baselines/all/coverage_summary.json",
)
REQUIRED_TREES = ("cache", "baselines/5000", "baselines/20000", "baselines/all")


def validate_release_fixture(fixture_root: Path, work_dir: Path) -> dict[str, Any]:
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
        raise ValueError("Release fixture is missing file bindings: " + ", ".join(missing_files))
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
        directory = _safe_path(root, relative)
        observed_hash, observed_count = _tree_digest(directory)
        if observed_hash != binding.get("sha256") or observed_count != binding.get("file_count"):
            raise ValueError(f"Release fixture tree hash mismatch: {relative}")
        checked_trees[relative] = {"sha256": observed_hash, "file_count": observed_count}

    cache = InferenceCache(root / "cache", offline=True)
    try:
        integrity = cache.integrity_check()
        if integrity != "ok":
            raise ValueError(f"Release cache integrity failed: {integrity}")
        cache_stats = cache.stats()
        cached_profile_ids = cache.qualification_profile_ids()
    finally:
        cache.close()

    profile = load_automation_profile(root / "automation_profile.json")
    primary_manifest = load_model_manifest(root / "primary_model_manifest.json")
    verifier_manifest = load_model_manifest(root / "verifier_model_manifest.json")
    validate_consensus_model_pair(primary_manifest, verifier_manifest)
    load_compute_profile(root / "compute_profile.json")
    qualification = _read_json(root / "qualification_report.json")
    if profile.status != "AUTOMATION_VALIDATED" or qualification.get("status") != "AUTOMATION_VALIDATED":
        raise ValueError("Release fixture qualification is not AUTOMATION_VALIDATED")
    if qualification.get("profile_id") != profile.profile_id:
        raise ValueError("Qualification report and automation profile do not match")
    if profile.primary_manifest_id != primary_manifest.manifest_id:
        raise ValueError("Primary manifest does not match automation profile")
    if profile.primary_manifest_sha256 != manifest_sha256(primary_manifest):
        raise ValueError("Primary manifest content does not match automation profile")
    if profile.verifier_manifest_id != verifier_manifest.manifest_id:
        raise ValueError("Verifier manifest does not match automation profile")
    if profile.verifier_manifest_sha256 != manifest_sha256(verifier_manifest):
        raise ValueError("Verifier manifest content does not match automation profile")
    if profile.profile_id not in cached_profile_ids:
        raise ValueError("Release cache does not contain the automation profile")
    cases_db = DuckDB()
    try:
        case_rows = cases_db.rows(
            f"SELECT {', '.join(QUALIFICATION_CASE_COLUMNS)} "
            f"FROM read_parquet('{q(root / 'qualification_cases.parquet')}') "
            "ORDER BY case_id"
        )
    finally:
        cases_db.close()
    if qualification_case_set_hash(case_rows) != profile.case_set_hash:
        raise ValueError("Qualification cases do not match the automation profile")
    if qualification.get("case_set_hash") != profile.case_set_hash:
        raise ValueError("Qualification report case set does not match the profile")

    expected_hashes = _read_json(root / "expected_artifact_hashes.json")
    for envelope in ("5000", "20000", "all"):
        baseline_manifest = _read_json(root / "baselines" / envelope / "build_manifest.json")
        if baseline_manifest.get("version") != __version__:
            raise ValueError(f"The {envelope} baseline version is incompatible")
        inference = baseline_manifest.get("inference")
        if not isinstance(inference, dict) or inference.get("automation_profile_id") != profile.profile_id:
            raise ValueError(f"The {envelope} baseline profile does not match")
        stats = baseline_manifest.get("stats")
        if not isinstance(stats, dict) or stats.get("qualification_status") != "AUTOMATION_VALIDATED":
            raise ValueError(f"The {envelope} baseline is not automation validated")
        expected_artifacts = expected_hashes.get(envelope)
        if not isinstance(expected_artifacts, dict) or expected_artifacts != baseline_manifest.get("artifact_hashes"):
            raise ValueError(f"The {envelope} baseline artifact hashes do not match")
        if envelope == "all":
            coverage = _read_json(root / "baselines" / envelope / "coverage_summary.json")
            if coverage.get("all_market_selection") is not True:
                raise ValueError("The all-market baseline is not a full-catalog selection")
            selection = coverage.get("input_selection")
            if (
                int(coverage.get("markets") or 0) != 94_777
                or int(coverage.get("propositions") or 0) != 189_570
                or not isinstance(selection, dict)
                or int(selection.get("input_market_rows") or 0) != 94_781
                or int(selection.get("invalid_market_rows") or 0) != 4
            ):
                raise ValueError("The all-market baseline catalog counts are not canonical")
            viewer = _read_json(root / "baselines" / envelope / "viewer_manifest.json")
            if not viewer.get("graph_content_fingerprint"):
                raise ValueError("The all-market baseline is missing its viewer fingerprint")
    performance = _read_json(root / "performance_report.json")
    if performance.get("passed") is not True:
        raise ValueError("Release performance gates did not pass")

    result = {
        "passed": True,
        "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
        "checked_files": checked_files,
        "checked_trees": checked_trees,
        "cache": cache_stats,
        "decision": "AUTOMATION_VALIDATED",
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
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
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
