from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_discovery_release import (
    CANONICAL_SOURCE_SHA256,
    FIXTURE_SCHEMA_VERSION,
    PACKAGE_VERSION,
    REQUIRED_FILES,
    REQUIRED_TREES,
    _baseline_requested_models,
    _sha256,
    _tree_provenance,
    _validate_fixture_manifest,
)
from scripts.benchmark_discovery import _acceptance


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "fixture"
    for relative in REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    cache_file = root / "cache" / "entry.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{}", encoding="utf-8")
    files = {
        relative: _sha256(root / relative)
        for relative in REQUIRED_FILES
    }
    trees = {
        relative: _tree_provenance(root / relative)
        for relative in REQUIRED_TREES
    }
    manifest: dict[str, object] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "source_sha256": CANONICAL_SOURCE_SHA256,
        "files": files,
        "trees": trees,
    }
    path = root / "fixture-manifest.json"
    path.write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )
    return root, path, manifest


def test_release_fixture_manifest_requires_every_provenance_binding(
    tmp_path: Path,
) -> None:
    root, path, manifest = _fixture(tmp_path)
    files = dict(manifest["files"])  # type: ignore[arg-type]
    files.pop("compute-profile.json")
    manifest["files"] = files
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required file bindings"):
        _validate_fixture_manifest(root, path)


def test_release_fixture_manifest_rejects_path_traversal(
    tmp_path: Path,
) -> None:
    root, path, manifest = _fixture(tmp_path)
    files = dict(manifest["files"])  # type: ignore[arg-type]
    files["../outside"] = "0" * 64
    manifest["files"] = files
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsafe release fixture path"):
        _validate_fixture_manifest(root, path)


def test_release_replay_uses_baseline_selected_models() -> None:
    assert _baseline_requested_models(
        {
            "models": {
                "parse": {"requested": "open/challenger-parser"},
                "classify": {"requested": "open/challenger-classifier"},
            }
        }
    ) == ("open/challenger-parser", "open/challenger-classifier")
    with pytest.raises(ValueError, match="requested classify model"):
        _baseline_requested_models(
            {
                "models": {
                    "parse": {"requested": "open/parser"},
                    "classify": {"observed": ["open/classifier"]},
                }
            }
        )


def test_performance_speed_gate_applies_only_to_release_envelopes() -> None:
    small = {
        "500:clean": {
            "median_candidate_seconds": 0.1,
        },
        "500:one-market-incremental": {
            "median_candidate_seconds": 0.2,
        },
        "500:offline": {
            "median_candidate_seconds": 0.01,
        },
    }
    small_result = _acceptance(small, baseline=None)
    assert "500:incremental_candidate_faster" not in small_result["gates"]
    assert small_result["passed"] is True

    release = {
        "5000:clean": {
            "median_candidate_seconds": 1.0,
        },
        "5000:one-market-incremental": {
            "median_candidate_seconds": 0.96,
        },
    }
    release_result = _acceptance(release, baseline=None)
    assert release_result["gates"]["5000:incremental_candidate_faster"] is False
    assert release_result["passed"] is False
