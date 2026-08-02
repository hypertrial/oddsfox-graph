from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oddsfox_graph._discovery import versions as discovery_versions
from oddsfox_graph._discovery.manifest_contracts import (
    DeadlineSummary,
    FastBuildManifest,
    InputBinding,
    ResponseLimits,
    ViewerInputBinding,
    ViewerManifest,
    WC2026InputSelection,
    WC2026Scope,
    current_version_bindings,
    load_build_manifest,
    resolved_scope,
    validate_manifest_pair,
)
from oddsfox_graph._discovery.versions import discovery_semantics_fingerprint


_HASH = "1" * 64
_OTHER_HASH = "2" * 64


def _selection() -> WC2026InputSelection:
    return WC2026InputSelection(
        strategy="all_valid_pipeline_wc2026_markets",
        source="oddsfox-pipeline",
        scope="wc2026",
        universe="knockout_progression",
        selection="all_valid_pipeline_wc2026_markets",
        adapter_version="input-adapters-v2",
        input_hourly_rows=20,
        input_rows=20,
        input_market_rows=2,
        input_propositions=4,
        invalid_market_rows=0,
        eligible_markets=2,
        eligible_propositions=4,
        selected_markets=2,
        selected_propositions=4,
        teams=2,
        stages=1,
        stage_keys=("winner",),
        first_hour_epoch=1,
        last_hour_epoch=2,
        normalized_semantic_fingerprint=_HASH,
        truncated=False,
    )


def _input() -> InputBinding:
    return InputBinding(
        path="/source/wc.parquet",
        sha256=_HASH,
        schema="polymarket-wc2026-graph-hourly-v1",
        profile="polymarket-wc2026-graph-hourly-v1",
        normalized_semantic_fingerprint=_HASH,
        selection=_selection(),
    )


def _build() -> FastBuildManifest:
    return FastBuildManifest(
        schema_version="graph-build-manifest-v1",
        command="discover",
        version="0.13.0",
        build_mode="fast",
        validation_status="DETERMINISTIC_VALIDATED",
        deadline=DeadlineSummary(
            seconds=120.0,
            elapsed_seconds=1.0,
            met=True,
            cutoff_triggered=False,
            assessed_pairs=3,
            unassessed_pairs=0,
        ),
        input=_input(),
        scope=WC2026Scope(),
        source_tree_fingerprint=_HASH,
        discovery_semantics_fingerprint=_HASH,
        graph_content_fingerprint=_HASH,
        versions=current_version_bindings(),
        artifacts=("nodes.parquet", "state/example.parquet"),
        artifact_hashes={"nodes.parquet": _HASH},
        state_hashes={"state/example.parquet": _OTHER_HASH},
        published_file_hashes={
            "nodes.parquet": _HASH,
            "state/example.parquet": _OTHER_HASH,
        },
        stats={"markets": 2, "complete": True},
    )


def _viewer() -> ViewerManifest:
    return ViewerManifest(
        schema_version="viewer-artifacts-v4",
        api_version="viewer-api-v4",
        layout_version="visualization-layout-v2",
        build_mode="fast",
        validation_status="DETERMINISTIC_VALIDATED",
        input_profile="polymarket-wc2026-graph-hourly-v1",
        input=ViewerInputBinding(
            sha256=_HASH,
            normalized_semantic_fingerprint=_HASH,
        ),
        scope=WC2026Scope(),
        source_tree_fingerprint=_HASH,
        discovery_semantics_fingerprint=_HASH,
        source_watermark="2026-01-01T00:00:00+00:00",
        graph_content_fingerprint=_HASH,
        response_limits=ResponseLimits(nodes=5_000, edges=10_000),
        evidence_tiers=("source_contract", "deterministic_rule"),
    )


def test_manifest_round_trip_is_strict_and_mode_independent(tmp_path: Path) -> None:
    path = tmp_path / "build_manifest.json"
    path.write_text(_build().model_dump_json(), encoding="utf-8")

    loaded = load_build_manifest(path)

    assert loaded == _build()
    assert resolved_scope(loaded.input.selection) == loaded.scope
    validate_manifest_pair(loaded, _viewer())


def test_manifest_rejects_legacy_shape_and_incomplete_hash_inventory(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "command": "discover",
                "version": "0.12.0",
                "build_mode": "fast",
                "input_hash": _HASH,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="clean v0.13"):
        load_build_manifest(legacy)

    payload = _build().model_dump(mode="json")
    payload["published_file_hashes"] = {"nodes.parquet": _HASH}
    with pytest.raises(ValidationError, match="exactly cover"):
        FastBuildManifest.model_validate_json(json.dumps(payload))


def test_manifest_rejects_cross_file_fingerprint_drift() -> None:
    viewer = _viewer().model_copy(
        update={"graph_content_fingerprint": _OTHER_HASH}
    )
    with pytest.raises(ValueError, match="graph content fingerprint"):
        validate_manifest_pair(_build(), viewer)

    viewer = _viewer().model_copy(update={"source_tree_fingerprint": _OTHER_HASH})
    with pytest.raises(ValueError, match="source-tree audit fingerprint"):
        validate_manifest_pair(_build(), viewer)


def test_input_binding_rejects_profile_selection_mismatch() -> None:
    with pytest.raises(ValidationError, match="profile must match"):
        InputBinding(
            path="/source/wc.parquet",
            sha256=_HASH,
            schema="polymarket-wc2026-graph-hourly-v1",
            profile="polymarket-market-snapshot-v1",
            normalized_semantic_fingerprint=_HASH,
            selection=_selection(),
        )


@pytest.mark.parametrize(
    "binding",
    (
        "DOMAIN_TAXONOMY_VERSION",
        "RETRIEVAL_VERSION",
        "PARSE_FALLBACK_VERSION",
        "ANN_INDEX_VERSION",
        "PARSE_PROMPT_VERSION",
        "CLASSIFY_PROMPT_VERSION",
    ),
)
def test_semantics_fingerprint_binds_full_mode_versions(
    binding: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = discovery_semantics_fingerprint()

    monkeypatch.setattr(discovery_versions, binding, f"changed-{binding}")

    assert discovery_semantics_fingerprint() != baseline


@pytest.mark.parametrize(
    ("binding", "value"),
    (
        ("domain_taxonomy", "stale-domains"),
        ("retrieval", "stale-retrieval"),
        ("parse_fallback", "stale-fallback"),
        ("ann", {"implementation": "stale"}),
        ("cache", 0),
        ("rule_registry_hash", _OTHER_HASH),
    ),
)
def test_version_bindings_reject_stale_full_mode_extensions(
    binding: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=binding):
        current_version_bindings(**{binding: value})  # type: ignore[arg-type]
