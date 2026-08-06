"""Unit tests for explorer callback helpers (no Dash callback invocation)."""

from __future__ import annotations

from pathlib import Path

import pytest
from dash import no_update

from oddsgraph.explorer import VIEW_BRACKET, VIEW_TOPOLOGY
from oddsgraph.explorer.callbacks import (
    add_search_result,
    expand_neighbors,
    highlight_on_tap,
    load_view,
    open_in_topology,
)
from oddsgraph.explorer.data import clear_stores
from oddsgraph.export import export_graph_artifacts
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport
from tests.helpers import make_settings

dash = pytest.importorskip("dash")


@pytest.fixture(autouse=True)
def _clear_explorer_stores() -> None:
    clear_stores()
    yield
    clear_stores()


def _write_fixture(build_dir: Path) -> None:
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
        CanonicalNode(
            canonical_id="team:brazil",
            type=NodeType.TEAM,
            label="Brazil",
            aliases=["bra"],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
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
        CanonicalEdge(
            source_id="team:brazil",
            target_id="match:a",
            edge_type=EdgeType.PARTICIPATES_IN,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="Brazil plays",
            inference_method="deterministic",
        ),
    ]
    export_graph_artifacts(
        nodes=nodes,
        edges=edges,
        rejected_edges=[],
        report=InferenceReport(
            events_processed=1,
            node_counts={"STAGE": 2, "MATCH": 2, "TEAM": 1},
            edge_counts={"PART_OF": 2, "ADVANCES_TO": 1, "PARTICIPATES_IN": 1},
        ),
        nodes_path=build_dir / "nodes.parquet",
        edges_path=build_dir / "edges.parquet",
        rejected_edges_path=build_dir / "rejected_edges.parquet",
        ontology_path=build_dir / "ontology.json",
        inference_report_path=build_dir / "inference_report.json",
    )


def test_highlight_on_tap_updates_path_classes() -> None:
    elements = [
        {"data": {"id": "a", "type": "MATCH", "stage": "Round of 16"}, "classes": "MATCH"},
        {"data": {"id": "b", "type": "MATCH", "stage": "Final"}, "classes": "MATCH"},
        {
            "data": {
                "id": "a|ADVANCES_TO|b",
                "source": "a",
                "target": "b",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    result = highlight_on_tap(elements, {"id": "a"})
    assert result is not None
    highlighted, status, *_rest = result
    by_id = {el["data"]["id"]: el for el in highlighted}
    assert "path-active" in by_id["a"]["classes"].split()
    assert "path-active" in by_id["b"]["classes"].split()
    assert "Highlighted path through a." in status


def test_expand_from_bracket_sets_topology(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    (
        elements,
        status,
        next_types,
        layout,
        view_mode,
        _stylesheet,
        selected,
        _edge,
        skip,
    ) = expand_neighbors(
        settings,
        "match:a",
        VIEW_BRACKET,
        elements=[],
        visible_types=["MATCH"],
        min_confidence=0.0,
        inference_method="",
    )
    assert view_mode == VIEW_TOPOLOGY
    assert layout == "breadthfirst"
    assert selected == "match:a"
    assert skip is True
    assert "MATCH" in next_types
    assert "TEAM" in next_types
    assert "Expanded match:a in Full topology" in status
    ids = {el["data"]["id"] for el in elements if "source" not in (el.get("data") or {})}
    assert "match:a" in ids
    assert "team:brazil" in ids


def test_expand_surfaces_neighbor_truncation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    _elements, status, *_rest = expand_neighbors(
        settings,
        "match:a",
        VIEW_TOPOLOGY,
        elements=[],
        visible_types=["MATCH", "TEAM", "STAGE"],
        min_confidence=0.0,
        inference_method="",
        neighbor_limit=1,
    )
    assert "Truncated to 1 neighbors" in status


def test_load_view_honors_skip_reload(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    skipped = load_view(
        settings,
        VIEW_TOPOLOGY,
        0.0,
        "",
        skip_view_reload=True,
    )
    assert skipped[0] is no_update
    assert skipped[8] is False

    loaded = load_view(settings, VIEW_BRACKET, 0.0, "")
    assert loaded[0] is not no_update
    assert "Loaded knockout bracket view." in loaded[1]


def test_open_in_topology_and_search_switch_view(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    opened = open_in_topology(settings, "match:a", 0.0, "")
    assert opened[4] == VIEW_TOPOLOGY
    assert opened[8] is True
    assert "Opened match:a in Full topology." in opened[1]

    added = add_search_result(
        settings,
        "team:brazil",
        VIEW_BRACKET,
        elements=[],
        visible_types=["MATCH"],
        min_confidence=0.0,
        inference_method="",
    )
    assert added[4] == VIEW_TOPOLOGY
    assert added[6] == "team:brazil"
    assert "Opened team:brazil in Full topology." in added[1]
