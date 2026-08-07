"""Unit tests for explorer callback helpers (no Dash callback invocation)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from dash import no_update

from oddsgraph.explorer.callbacks import (
    apply_time_slider,
    highlight_on_tap,
    load_view,
    remove_from_canvas,
)
from oddsgraph.explorer.data import clear_stores
from oddsgraph.export import export_graph_artifacts
from oddsgraph.odds_history import ODDS_HISTORY_SCHEMA
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


def test_highlight_on_tap_marks_path() -> None:
    elements = [
        {"data": {"id": "a", "type": "MATCH", "label": "A"}, "classes": "MATCH"},
        {"data": {"id": "b", "type": "MATCH", "label": "B"}, "classes": "MATCH"},
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


def test_load_view_resets_bracket(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    loaded = load_view(settings, 0.0, "", reset=True)
    assert loaded[0] is not no_update
    assert "Reset knockout bracket view." in loaded[1]
    ids = {
        el["data"]["id"]
        for el in loaded[0]
        if "source" not in (el.get("data") or {})
        and el["data"].get("type") == "MATCH"
    }
    assert ids == {"match:a", "match:b"}


def test_apply_time_slider_locks_winner(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "match_canonical_id": "match:a",
                    "home_team": "Brazil",
                    "away_team": "France",
                    "odds_hour_epoch": 100,
                    "home_prob": 0.4,
                    "away_prob": 0.6,
                    "match_start_epoch": 100,
                    "match_end_epoch": 200,
                    "winner_team": "France",
                },
                {
                    "match_canonical_id": "match:a",
                    "home_team": "Brazil",
                    "away_team": "France",
                    "odds_hour_epoch": 150,
                    "home_prob": 0.2,
                    "away_prob": 0.8,
                    "match_start_epoch": 100,
                    "match_end_epoch": 200,
                    "winner_team": "France",
                },
            ],
            schema=ODDS_HISTORY_SCHEMA,
        ),
        settings.odds_history_path,
    )

    elements, *_ = load_view(settings, 0.0, "", hour_epoch=100)
    mid = apply_time_slider(elements, 150, 0.0, "")
    locked = apply_time_slider(elements, 250, 0.0, "")
    by_mid = {
        el["data"]["id"]: el["data"]
        for el in mid[0]
        if el["data"].get("type") == "MATCH"
    }
    by_locked = {
        el["data"]["id"]: el["data"]
        for el in locked[0]
        if el["data"].get("type") == "MATCH"
    }
    assert by_mid["match:a"]["current_home_prob"] == 0.2
    assert by_locked["match:a"]["current_home_prob"] == 0.0


def test_remove_from_canvas(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)
    elements, *_ = load_view(settings, 0.0, "")
    removed = remove_from_canvas("match:a", elements, 0.0, "")
    ids = {
        el["data"]["id"]
        for el in removed[0]
        if "source" not in (el.get("data") or {})
        and el["data"].get("type") == "MATCH"
    }
    assert "match:a" not in ids
    assert "match:b" in ids


def test_toggle_controls_and_inspector_classnames() -> None:
    """Sidebar toggles flip ``is-open`` class names used by CSS collapse."""

    def toggle(is_open: bool, *, kind: str) -> tuple[str, bool]:
        next_open = not bool(is_open)
        base = "explorer-controls" if kind == "controls" else "explorer-inspector"
        classes = f"{base} is-open" if next_open else base
        return classes, next_open

    classes, opened = toggle(True, kind="controls")
    assert opened is False
    assert classes == "explorer-controls"
    classes, opened = toggle(False, kind="controls")
    assert opened is True
    assert classes == "explorer-controls is-open"

    classes, opened = toggle(True, kind="inspector")
    assert opened is False
    assert classes == "explorer-inspector"
    classes, opened = toggle(False, kind="inspector")
    assert opened is True
    assert classes == "explorer-inspector is-open"
