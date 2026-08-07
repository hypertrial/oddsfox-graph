"""Smoke tests for the Dash explorer app layout (requires explore extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

from oddsgraph.export import export_graph_artifacts
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport
from tests.helpers import make_settings

dash = pytest.importorskip("dash")


def _write_minimal_build(build_dir: Path) -> None:
    nodes = [
        CanonicalNode(
            canonical_id="stage:world-cup-2026:round-of-16",
            type=NodeType.STAGE,
            label="Round of 16",
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="seed",
            inference_method="official_bracket",
        ),
        CanonicalNode(
            canonical_id="stage:world-cup-2026:final",
            type=NodeType.STAGE,
            label="Final",
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="seed",
            inference_method="official_bracket",
        ),
        CanonicalNode(
            canonical_id="match:a",
            type=NodeType.MATCH,
            label="Brazil vs. France",
            aliases=["fifa-match-1"],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="official_bracket",
        ),
        CanonicalNode(
            canonical_id="match:b",
            type=NodeType.MATCH,
            label="Spain vs. Germany",
            aliases=["fifa-match-2"],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="official_bracket",
        ),
    ]
    edges = [
        CanonicalEdge(
            source_id="match:a",
            target_id="stage:world-cup-2026:round-of-16",
            edge_type=EdgeType.PART_OF,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="Round of 16",
            inference_method="official_bracket",
        ),
        CanonicalEdge(
            source_id="match:b",
            target_id="stage:world-cup-2026:final",
            edge_type=EdgeType.PART_OF,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="Final",
            inference_method="official_bracket",
        ),
        CanonicalEdge(
            source_id="match:a",
            target_id="match:b",
            edge_type=EdgeType.ADVANCES_TO,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="continuity",
            inference_method="official_bracket",
        ),
    ]
    export_graph_artifacts(
        nodes=nodes,
        edges=edges,
        rejected_edges=[],
        report=InferenceReport(
            events_processed=1,
            node_counts={"STAGE": 2, "MATCH": 2},
            edge_counts={"PART_OF": 2, "ADVANCES_TO": 1},
        ),
        nodes_path=build_dir / "nodes.parquet",
        edges_path=build_dir / "edges.parquet",
        rejected_edges_path=build_dir / "rejected_edges.parquet",
        ontology_path=build_dir / "ontology.json",
        inference_report_path=build_dir / "inference_report.json",
    )


def test_build_app_registers_layout_assets_and_callbacks(tmp_path: Path) -> None:
    from oddsgraph.explorer.app import ASSETS_DIR, TIME_PLAY_MS_PER_STEP, build_app
    from oddsgraph.explorer.tree_render import build_connector_paths

    settings = make_settings(tmp_path)
    _write_minimal_build(settings.build_dir)

    assert ASSETS_DIR.exists()
    assert (ASSETS_DIR / "explorer.css").exists()
    assert TIME_PLAY_MS_PER_STEP == 400

    app = build_app(settings)
    assert app.title == "OddsFox Graph explorer"
    layout = app.layout
    assert layout is not None

    rendered = str(layout)
    for component_id in (
        "time-slider",
        "time-slider-label",
        "time-play-button",
        "time-play-interval",
        "time-play-state",
        "bracket-canvas",
        "reset-button",
        "toggle-controls",
        "confidence-filter",
        "stage-tracker",
        "phase-badge",
        "bracket-summary",
        "controls-panel",
        "phase-key",
        "close-controls",
        "match-modal",
        "match-modal-scrim",
        "match-modal-close",
        "match-modal-graph",
        "match-modal-title",
    ):
        assert component_id in rendered

    assert "controls-open" in rendered
    assert "Filters & legend" in rendered
    assert "explorer-stage" in rendered
    assert "playback-dock" in rendered
    assert "graph-cyto" not in rendered
    assert "inspector-panel" not in rendered
    assert "toggle-inspector" not in rendered
    assert "Hide match" not in rendered
    assert "hover-card" not in rendered
    assert "action-status" in rendered
    assert "lock to 100% / 0%" in rendered
    assert (ASSETS_DIR / "oddsfox-favicon.png").exists()
    assert (ASSETS_DIR / "inter-latin-variable.woff2").exists()
    assert (ASSETS_DIR / "jetbrains-mono-latin-variable.woff2").exists()

    def _find_component(node: object, target: str) -> object | None:
        if getattr(node, "id", None) == target:
            return node
        children = getattr(node, "children", None)
        if children is None:
            return None
        if not isinstance(children, (list, tuple)):
            children = [children]
        for child in children:
            if child is None or isinstance(child, (str, int, float, bool)):
                continue
            found = _find_component(child, target)
            if found is not None:
                return found
        return None

    time_slider = _find_component(layout, "time-slider")
    confidence = _find_component(layout, "confidence-filter")
    assert time_slider is not None and confidence is not None
    assert getattr(time_slider, "allow_direct_input") is False
    assert getattr(confidence, "allow_direct_input") is False
    assert getattr(time_slider, "tooltip") is False
    assert getattr(confidence, "tooltip") is False
    assert getattr(time_slider, "step") is None

    for removed_id in (
        "view-mode",
        "search-input",
        "view-in-topology-button",
        "layout-dropdown",
        "type-filter",
        "skip-view-reload",
        "expand-button",
        "remove-button",
        "graph-cyto",
    ):
        assert removed_id not in rendered

    # Utility drawer defaults closed (no is-open on controls panel class).
    assert "explorer-controls is-open" not in rendered

    paths = build_connector_paths(4, "ltr")
    assert len(paths) == 2
    assert paths[0].startswith("M 0")

    assert len(app.callback_map) >= 5
