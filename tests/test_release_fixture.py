from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_discovery_release import (
    CANONICAL_MARKET_ROWS,
    CANONICAL_SOURCE_SHA256,
    FIXTURE_SCHEMA_VERSION,
    PACKAGE_VERSION,
    REQUIRED_FILES,
    REQUIRED_TREES,
    _baseline_requested_models,
    _sha256,
    _tree_provenance,
    _validate_canonical_catalog,
    _validate_fixture_manifest,
)
from scripts.benchmark_discovery import _acceptance, _summaries


ROOT = Path(__file__).resolve().parents[1]
REAL_INPUT = ROOT / "data" / "polymarket_all_markets_20260730T093857Z.parquet"
requires_real_catalog = pytest.mark.skipif(
    not REAL_INPUT.is_file(),
    reason="canonical release catalog is an external fixture",
)


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


@requires_real_catalog
def test_release_catalog_contract_is_current() -> None:
    assert CANONICAL_MARKET_ROWS == 94_781
    _validate_canonical_catalog(REAL_INPUT)


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
    small_result = _acceptance(small)
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
    release_result = _acceptance(release)
    assert release_result["gates"]["5000:incremental_candidate_faster"] is False
    assert release_result["passed"] is False


def test_performance_summary_records_absolute_throughput_and_candidates() -> None:
    summary = _summaries(
        [
            {
                "size": 5_000,
                "mode": "clean",
                "wall_seconds": 10.0,
                "peak_rss_mb": 512.0,
                "propositions_per_second": 500.0,
                "candidate_edges": 40_000,
                "stage_timings": {
                    "generate_candidates": 2.0,
                    "publish_artifacts": 3.0,
                },
            }
        ]
    )
    clean = summary["5000:clean"]
    assert clean["median_propositions_per_second"] == 500.0
    assert clean["median_candidate_edges"] == 40_000
