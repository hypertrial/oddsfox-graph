"""Tests for explorer presentation helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.explorer.presentation import (
    apply_path_highlight,
    bracket_layout,
    bracket_positions,
    bracket_stylesheet,
    fifa_match_id,
    short_match_label,
    stage_column,
    stage_rank,
    stylesheet_for,
)


def test_short_match_label_splits_vs() -> None:
    assert short_match_label("Brazil vs. France") == "Brazil\nFrance"
    assert short_match_label("Mexico vs South Africa") == "Mexico\nSouth Africa"
    assert short_match_label("Finalist TBD") == "Finalist TBD"


def test_fifa_match_id_and_stage_helpers() -> None:
    assert fifa_match_id(["fifa-match-73", "other"]) == 73
    assert fifa_match_id(["nope"]) is None
    assert stage_rank("Round of 32") == 1
    assert stage_rank("Final") == 5
    assert stage_rank("Third Place") == 5
    assert stage_column("Round of 32") == 0
    assert stage_column("Final") == stage_column("Third Place") == 4


def test_bracket_positions_lr_midpoints_and_third_place() -> None:
    nodes = [
        {
            "data": {
                "id": "m1",
                "label": "A vs. B",
                "stage": "Round of 32",
                "aliases": ["fifa-match-1"],
            }
        },
        {
            "data": {
                "id": "m2",
                "label": "C vs. D",
                "stage": "Round of 32",
                "aliases": ["fifa-match-2"],
            }
        },
        {
            "data": {
                "id": "qf",
                "label": "Winner1 vs. Winner2",
                "stage": "Round of 16",
                "aliases": ["fifa-match-10"],
            }
        },
        {
            "data": {
                "id": "final",
                "label": "F1 vs. F2",
                "stage": "Final",
                "aliases": ["fifa-match-100"],
            }
        },
        {
            "data": {
                "id": "third",
                "label": "T1 vs. T2",
                "stage": "Third Place",
                "aliases": ["fifa-match-99"],
            }
        },
    ]
    edges = [
        {"data": {"source": "m1", "target": "qf", "edge_type": "ADVANCES_TO"}},
        {"data": {"source": "m2", "target": "qf", "edge_type": "ADVANCES_TO"}},
        {"data": {"source": "qf", "target": "final", "edge_type": "ADVANCES_TO"}},
    ]
    positions = bracket_positions(nodes, edges)
    assert positions["m1"]["x"] < positions["qf"]["x"] < positions["final"]["x"]
    assert positions["final"]["x"] == positions["third"]["x"]
    assert positions["m1"]["y"] < positions["m2"]["y"]
    expected_mid = (positions["m1"]["y"] + positions["m2"]["y"]) / 2
    assert abs(positions["qf"]["y"] - expected_mid) < 1e-6
    assert positions["third"]["y"] > positions["final"]["y"]


def test_apply_path_highlight_marks_ancestors_and_descendants() -> None:
    elements = [
        {"data": {"id": "a", "type": "MATCH", "stage": "Round of 32"}, "classes": "MATCH"},
        {"data": {"id": "b", "type": "MATCH", "stage": "Round of 16"}, "classes": "MATCH"},
        {"data": {"id": "c", "type": "MATCH", "stage": "Final"}, "classes": "MATCH"},
        {"data": {"id": "d", "type": "MATCH", "stage": "Round of 16"}, "classes": "MATCH"},
        {
            "data": {
                "id": "a|ADVANCES_TO|b",
                "source": "a",
                "target": "b",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "b|ADVANCES_TO|c",
                "source": "b",
                "target": "c",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "d|ADVANCES_TO|c",
                "source": "d",
                "target": "c",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    highlighted = apply_path_highlight(elements, "b")
    by_id = {el["data"]["id"]: el for el in highlighted}
    assert "path-active" in by_id["a"]["classes"].split()
    assert "path-active" in by_id["b"]["classes"].split()
    assert "path-active" in by_id["c"]["classes"].split()
    assert "path-muted" in by_id["d"]["classes"].split()
    assert "path-active" in by_id["a|ADVANCES_TO|b"]["classes"].split()
    assert "path-muted" in by_id["d|ADVANCES_TO|c"]["classes"].split()

    cleared = apply_path_highlight(highlighted, None)
    assert all(
        "path-active" not in (el.get("classes") or "").split()
        and "path-muted" not in (el.get("classes") or "").split()
        for el in cleared
    )


def test_apply_path_highlight_prefers_final_over_third_place() -> None:
    elements = [
        {"data": {"id": "sf", "type": "MATCH", "stage": "Semifinals"}, "classes": "MATCH"},
        {"data": {"id": "final", "type": "MATCH", "stage": "Final"}, "classes": "MATCH"},
        {
            "data": {"id": "third", "type": "MATCH", "stage": "Third Place"},
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "sf|ADVANCES_TO|final",
                "source": "sf",
                "target": "final",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "sf|ADVANCES_TO|third",
                "source": "sf",
                "target": "third",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    via_sf = apply_path_highlight(elements, "sf")
    by_id = {el["data"]["id"]: el for el in via_sf}
    assert "path-active" in by_id["final"]["classes"].split()
    assert "path-muted" in by_id["third"]["classes"].split()
    assert "path-active" in by_id["sf|ADVANCES_TO|final"]["classes"].split()
    assert "path-muted" in by_id["sf|ADVANCES_TO|third"]["classes"].split()

    via_third = apply_path_highlight(elements, "third")
    by_id = {el["data"]["id"]: el for el in via_third}
    assert "path-active" in by_id["third"]["classes"].split()
    assert "path-active" in by_id["sf"]["classes"].split()
    assert "path-muted" in by_id["final"]["classes"].split()
    assert "path-active" in by_id["sf|ADVANCES_TO|third"]["classes"].split()
    assert "path-muted" in by_id["sf|ADVANCES_TO|final"]["classes"].split()


def test_bracket_stylesheet_and_layout_contract() -> None:
    layout = bracket_layout()
    assert layout["name"] == "preset"
    styles = bracket_stylesheet()
    selectors = {rule["selector"] for rule in styles}
    assert "node" in selectors
    assert "edge" in selectors
    assert ".path-active" in selectors
    assert ".path-muted" in selectors
    edge_style = next(rule["style"] for rule in styles if rule["selector"] == "edge")
    assert edge_style["curve-style"] == "taxi"
    assert edge_style["label"] == ""
    node_style = next(rule["style"] for rule in styles if rule["selector"] == "node")
    assert node_style["shape"] == "round-rectangle"
    assert node_style["label"] == "data(short_label)"


def test_stylesheet_for_matches_bracket_and_topology() -> None:
    from oddsgraph.explorer import VIEW_BRACKET, VIEW_TOPOLOGY

    bracket = stylesheet_for(VIEW_BRACKET)
    topology = stylesheet_for(VIEW_TOPOLOGY)
    assert bracket == bracket_stylesheet()
    assert any(rule["selector"] == "edge" for rule in topology)
    assert bracket != topology
