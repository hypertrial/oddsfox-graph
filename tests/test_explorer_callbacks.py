"""Unit tests for explorer callback helpers (no Dash callback invocation)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from dash import no_update

from oddsgraph.explorer.callbacks import (
    apply_time_slider,
    load_view,
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


def test_load_view_resets_bracket(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture(settings.build_dir)

    loaded = load_view(settings, 0.0, "", reset=True)
    assert loaded[0] is not no_update
    assert "Reset knockout bracket view." in loaded[1]
    rendered = str(loaded[0])
    assert "bracket-root" in rendered or "match-card" in rendered


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

    mid = apply_time_slider(settings, 150, 0.0, "")
    locked = apply_time_slider(settings, 250, 0.0, "")
    mid_text = str(mid[0])
    locked_text = str(locked[0])
    # Both sides render: home 20% / away 80% mid-match.
    assert "20%" in mid_text
    assert "80%" in mid_text
    # After full-time the winner locks to 100% / 0%.
    assert "100%" in locked_text
    assert "0%" in locked_text
    assert "France" in locked_text


def test_toggle_controls_classnames() -> None:
    """Drawer toggle flips ``is-open`` class names used by CSS collapse."""

    def toggle(is_open: bool) -> tuple[str, bool]:
        next_open = not bool(is_open)
        base = "explorer-drawer explorer-controls"
        classes = f"{base} is-open" if next_open else base
        return classes, next_open

    classes, opened = toggle(True)
    assert opened is False
    assert classes == "explorer-drawer explorer-controls"
    classes, opened = toggle(False)
    assert opened is True
    assert classes == "explorer-drawer explorer-controls is-open"


def test_next_play_toggle_and_advance() -> None:
    from oddsgraph.explorer.callbacks import next_play_advance, next_play_toggle

    milestones = (100, 3700, 10_000)

    assert next_play_toggle(
        playing=False,
        hour_epoch=100,
        min_hour=0,
        max_hour=200,
        slider_disabled=True,
    ) == (True, "Play", False, None, "Play tournament timeline", "false")

    assert next_play_toggle(
        playing=True,
        hour_epoch=100,
        min_hour=0,
        max_hour=200,
        slider_disabled=False,
    ) == (True, "Play", False, None, "Play tournament timeline", "false")

    assert next_play_toggle(
        playing=False,
        hour_epoch=100,
        min_hour=0,
        max_hour=200,
        slider_disabled=False,
    ) == (False, "Pause", True, None, "Pause tournament timeline", "true")

    assert next_play_toggle(
        playing=False,
        hour_epoch=200,
        min_hour=0,
        max_hour=200,
        slider_disabled=False,
    ) == (False, "Pause", True, 0, "Pause tournament timeline", "true")

    assert (
        next_play_advance(
            playing=False, hour_epoch=100, max_hour=10_000, milestones=milestones
        )
        is None
    )
    assert next_play_advance(
        playing=True, hour_epoch=100, max_hour=10_000, milestones=milestones
    ) == (
        3700,
        False,
        "Pause",
        True,
        "Pause tournament timeline",
        "true",
    )
    assert next_play_advance(
        playing=True, hour_epoch=3700, max_hour=10_000, milestones=milestones
    ) == (
        10_000,
        True,
        "Play",
        False,
        "Play tournament timeline",
        "false",
    )
    # Mid-interval scrub values still jump forward to the next milestone.
    assert next_play_advance(
        playing=True, hour_epoch=2000, max_hour=10_000, milestones=milestones
    ) == (
        3700,
        False,
        "Pause",
        True,
        "Pause tournament timeline",
        "true",
    )
    assert (
        next_play_advance(
            playing=True, hour_epoch=100, max_hour=10_000, milestones=()
        )
        is None
    )


def test_phase_view_update_skips_tracker_when_unchanged() -> None:
    from dash import no_update

    from oddsgraph.bracket import schedule_stage_windows
    from oddsgraph.explorer.callbacks import phase_view_update

    windows = {w.stage_key: w for w in schedule_stage_windows()}
    hour = windows["round_of_32"].start_epoch
    first = phase_view_update(hour_epoch=hour, previous_phase_key=None)
    assert first[3].startswith("active:round_of_32")
    assert first[2] is not no_update
    assert "resolved matches" not in first[4]
    second = phase_view_update(hour_epoch=hour + 3600, previous_phase_key=first[3])
    assert second[2] is no_update
    assert second[3] == first[3]
    assert "Selected time" in second[4]


def test_match_modal_update_open_and_close(tmp_path: Path) -> None:
    from dash.exceptions import PreventUpdate

    from oddsgraph.explorer.callbacks import match_modal_update
    from oddsgraph.explorer.canvas_actions import find_projected_match

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
                    "home_prob": 0.55,
                    "away_prob": 0.45,
                    "match_start_epoch": 100,
                    "match_end_epoch": 200,
                    "winner_team": None,
                },
                {
                    "match_canonical_id": "match:a",
                    "home_team": "Brazil",
                    "away_team": "France",
                    "odds_hour_epoch": 150,
                    "home_prob": 0.62,
                    "away_prob": 0.38,
                    "match_start_epoch": 100,
                    "match_end_epoch": 200,
                    "winner_team": None,
                },
            ],
            schema=ODDS_HISTORY_SCHEMA,
        ),
        settings.odds_history_path,
    )

    data = find_projected_match(settings, "match:a", 150)
    assert data is not None
    assert data["home_team"] == "Brazil"

    opened = match_modal_update(
        triggered_id={
            "type": "match-card",
            "match_id": "match:a",
            "surface": "desktop",
        },
        settings=settings,
        hour_epoch=150,
    )
    assert opened[0] == "match-modal is-open"
    assert "Brazil vs France" in opened[1]
    assert opened[3] == "false"
    assert len(opened[2].data) == 2

    closed = match_modal_update(
        triggered_id="match-modal-close",
        settings=settings,
        hour_epoch=150,
    )
    assert closed[0] == "match-modal"
    assert closed[3] == "true"

    with pytest.raises(PreventUpdate):
        match_modal_update(
            triggered_id={"type": "match-card", "match_id": ""},
            settings=settings,
            hour_epoch=150,
        )

    with pytest.raises(PreventUpdate):
        match_modal_update(
            triggered_id={"type": "match-card", "match_id": "missing"},
            settings=settings,
            hour_epoch=150,
        )


def test_apply_time_slider_stamps_sparklines(tmp_path: Path) -> None:
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
                    "home_prob": 0.55,
                    "away_prob": 0.45,
                    "match_start_epoch": 100,
                    "match_end_epoch": None,
                    "winner_team": None,
                },
                {
                    "match_canonical_id": "match:a",
                    "home_team": "Brazil",
                    "away_team": "France",
                    "odds_hour_epoch": 160,
                    "home_prob": 0.60,
                    "away_prob": 0.40,
                    "match_start_epoch": 100,
                    "match_end_epoch": None,
                    "winner_team": None,
                },
            ],
            schema=ODDS_HISTORY_SCHEMA,
        ),
        settings.odds_history_path,
    )
    children, _ = apply_time_slider(settings, 160, 0.0, "")
    assert "match-team-sparkline" in str(children)
