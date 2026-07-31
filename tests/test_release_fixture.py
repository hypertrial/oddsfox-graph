from __future__ import annotations

import json
from pathlib import Path

import pytest

from oddsfox_graph._discovery.cache import InferenceCache
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
    _validate_performance_report,
)
from scripts.benchmark_discovery import (
    _acceptance,
    _load_budget,
    _summaries,
)


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
    cache = InferenceCache(root / "cache")
    cache.close()
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
                "cache": {"entry_count": 100, "storage_bytes": 4096},
                "candidate_workspace": {"database_bytes": 8192},
                "publication_bytes": 16384,
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
    assert clean["median_cache_entries"] == 100
    assert clean["median_workspace_bytes"] == 8192
    assert clean["median_duckdb_spill_bytes"] == 0
    assert clean["median_state_rows"] == {}


def test_performance_budget_reports_before_and_after_values() -> None:
    summary = {
        "5000:clean": {
            "median_wall_seconds": 9.0,
            "median_candidate_seconds": 2.0,
        },
        "20000:clean": {
            "median_peak_rss_mb": 1600.0,
            "median_publication_seconds": 3.0,
            "median_candidate_seconds": 4.0,
        },
    }
    budget = {
        "gates": {
            "max_5000_clean_wall_seconds": 10.32,
            "max_20000_clean_peak_rss_mb": 1688.0,
            "max_20000_publication_seconds": 3.67,
        },
        "before": {
            "5000_clean_wall_seconds": 12.9,
            "20000_clean_peak_rss_mb": 2110.0,
            "20000_publication_seconds": 4.59,
        },
    }

    result = _acceptance(summary, budget)

    comparison = result["performance_comparison"]
    assert comparison["5000:clean_wall_seconds"] == {
        "before": 12.9,
        "after": 9.0,
        "budget": 10.32,
        "unit": "seconds",
        "passed": True,
    }


def test_checked_in_performance_budget_is_bound_to_m4_and_three_runs() -> None:
    path = ROOT / "benchmarks" / "m4-v0.8-performance-budget.json"
    hardware = {
        "system": "Darwin",
        "machine": "arm64",
        "processor": "Apple M4",
    }

    budget = _load_budget(path, repetitions=3, hardware=hardware)

    assert budget["schema_version"] == "performance-budget-v1"
    with pytest.raises(ValueError, match="does not match"):
        _load_budget(path, repetitions=1, hardware=hardware)


def test_release_content_rejects_unpassed_performance_report(
    tmp_path: Path,
) -> None:
    performance = {
        "fake_runtime_version": "fake-runtime-v2",
        "repetitions": 3,
        "sizes": [5_000, 20_000],
        "modes": ["clean", "offline", "one-market-incremental"],
        "hardware": {
            "system": "Darwin",
            "machine": "arm64",
            "processor": "Apple M4",
        },
        "performance_budget": {
            **json.loads(
                (
                    ROOT
                    / "benchmarks"
                    / "m4-v0.8-performance-budget.json"
                ).read_text(encoding="utf-8")
            ),
        },
        "acceptance": {"passed": False},
    }
    performance_path = tmp_path / "performance-report.json"
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    with pytest.raises(ValueError, match="did not pass"):
        _validate_performance_report(performance_path)


def test_release_performance_report_requires_complete_equivalent_runs(
    tmp_path: Path,
) -> None:
    artifact_hashes = {"logic_edges.parquet": "a" * 64}
    modes = ["clean", "offline", "one-market-incremental"]
    samples = [
        {
            "repetition": repetition,
            "size": size,
            "mode": mode,
            "artifact_hashes": artifact_hashes,
            "wall_seconds": 9.0,
            "peak_rss_mb": 1_500.0,
            "propositions_per_second": 500.0,
            "candidate_edges": 1_000,
            "cache": {"entry_count": 100, "storage_bytes": 4_096},
            "candidate_workspace": {"database_bytes": 8_192},
            "publication_bytes": 16_384,
            "stage_timings": {
                "generate_candidates": (
                    2.0
                    if mode == "clean"
                    else (0.4 if mode == "offline" else 1.5)
                ),
                "publish_artifacts": 3.0,
            },
            **(
                {"equivalent_full_artifact_hashes": artifact_hashes}
                if mode == "one-market-incremental"
                else {}
            ),
        }
        for repetition in range(1, 4)
        for size in (5_000, 20_000)
        for mode in modes
    ]
    budget = json.loads(
        (
            ROOT / "benchmarks" / "m4-v0.8-performance-budget.json"
        ).read_text(encoding="utf-8")
    )
    summary = _summaries(samples)
    acceptance = _acceptance(summary, budget)
    performance = {
        "fake_runtime_version": "fake-runtime-v2",
        "repetitions": 3,
        "sizes": [5_000, 20_000],
        "modes": modes,
        "hardware": {
            "system": "Darwin",
            "machine": "arm64",
            "processor": "Apple M4",
        },
        "performance_budget": budget,
        "samples": samples,
        "summary": summary,
        "acceptance": acceptance,
    }
    performance_path = tmp_path / "performance-report.json"
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    _validate_performance_report(performance_path)

    performance["performance_budget"]["gates"][
        "max_5000_clean_wall_seconds"
    ] = 999.0
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    with pytest.raises(ValueError, match="did not pass"):
        _validate_performance_report(performance_path)

    performance["performance_budget"] = json.loads(
        (
            ROOT / "benchmarks" / "m4-v0.8-performance-budget.json"
        ).read_text(encoding="utf-8")
    )
    samples[-1]["equivalent_full_artifact_hashes"] = {
        "logic_edges.parquet": "b" * 64
    }
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    with pytest.raises(ValueError, match="incremental and clean-build"):
        _validate_performance_report(performance_path)

    samples[-1]["equivalent_full_artifact_hashes"] = artifact_hashes
    for sample in samples:
        if sample["size"] == 5_000 and sample["mode"] == "clean":
            sample["wall_seconds"] = 999.0
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    with pytest.raises(ValueError, match="measurements did not pass"):
        _validate_performance_report(performance_path)
