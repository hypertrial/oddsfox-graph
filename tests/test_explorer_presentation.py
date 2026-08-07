"""Tests for explorer presentation helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.explorer.presentation import (
    apply_path_highlight,
    apply_time_slice,
    bracket_layout,
    bracket_positions,
    bracket_stage_headers,
    bracket_stylesheet,
    fifa_match_id,
    format_hour_label,
    home_prob_at_hour,
    is_stage_header,
    short_match_label,
    stage_column,
    stage_rank,
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


def test_bracket_stage_headers_align_to_columns() -> None:
    headers = bracket_stage_headers()
    assert len(headers) == 5
    assert [h["data"]["label"] for h in headers] == [
        "Round of 32",
        "Round of 16",
        "Quarterfinals",
        "Semifinals",
        "Final / 3rd",
    ]
    assert all(is_stage_header(h) for h in headers)
    assert all(h["selectable"] is False and h["grabbable"] is False for h in headers)
    assert headers[0]["position"]["x"] < headers[3]["position"]["x"]
    assert headers[3]["data"]["label"] == "Semifinals"
    # Final / 3rd shares the terminal column with Final matches.
    assert stage_column("Final") == 4
    assert headers[4]["position"]["x"] == headers[0]["position"]["x"] + 4 * (
        headers[1]["position"]["x"] - headers[0]["position"]["x"]
    )

    only_terminal = bracket_stage_headers(columns={4})
    assert len(only_terminal) == 1
    assert only_terminal[0]["data"]["label"] == "Final / 3rd"


def test_apply_path_highlight_keeps_stage_headers_visible() -> None:
    elements = [
        {"data": {"id": "a", "type": "MATCH", "stage": "Round of 32"}, "classes": "MATCH"},
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
        *bracket_stage_headers(columns={0, 4}),
    ]
    highlighted = apply_path_highlight(elements, "a")
    by_id = {el["data"]["id"]: el for el in highlighted}
    assert "path-active" in by_id["a"]["classes"].split()
    header = by_id["stage-header:0"]
    assert "path-muted" not in header["classes"].split()
    assert "path-active" not in header["classes"].split()
    assert is_stage_header(header)


def test_bracket_stylesheet_and_layout_contract() -> None:
    layout = bracket_layout()
    assert layout["name"] == "preset"
    styles = bracket_stylesheet()
    selectors = {rule["selector"] for rule in styles}
    assert "node" in selectors
    assert "edge" in selectors
    assert ".path-active" in selectors
    assert ".path-muted" in selectors
    assert ".stage-header" in selectors
    edge_style = next(rule["style"] for rule in styles if rule["selector"] == "edge")
    assert edge_style["curve-style"] == "taxi"
    assert edge_style["label"] == ""
    node_style = next(rule["style"] for rule in styles if rule["selector"] == "node")
    assert node_style["shape"] == "round-rectangle"
    assert node_style["label"] == "data(short_label)"
    assert node_style["background-image"] == "data(flag_images)"
    assert node_style["width"] >= 200
    header_style = next(
        rule["style"] for rule in styles if rule["selector"] == ".stage-header"
    )
    assert header_style["events"] == "no"
    assert header_style["label"] == "data(label)"
    assert "node[current_home_prob]" in selectors
    odds_style = next(
        rule["style"]
        for rule in styles
        if rule["selector"] == "node[current_home_prob]"
    )
    assert "mapData(current_home_prob" in odds_style["background-color"]


def test_home_prob_at_hour_and_winner_lock() -> None:
    data = {
        "home_team": "Brazil",
        "away_team": "France",
        "match_end_epoch": 200,
        "winner_team": "France",
        "odds_series": [
            {"h": 100, "home": 0.4, "away": 0.6},
            {"h": 150, "home": 0.25, "away": 0.75},
        ],
    }
    assert home_prob_at_hour(data, 120) == 0.4
    assert home_prob_at_hour(data, 150) == 0.25
    assert home_prob_at_hour(data, 250) == 0.0
    data["winner_team"] = "Brazil"
    assert home_prob_at_hour(data, 250) == 1.0


def test_home_prob_at_hour_no_lookahead_before_first_point() -> None:
    data = {
        "home_team": "Spain",
        "away_team": "Argentina",
        "odds_series": [{"h": 200, "home": 0.7, "away": 0.3}],
    }
    assert home_prob_at_hour(data, 100) is None
    assert home_prob_at_hour(data, 200) == 0.7


def test_apply_time_slice_stamps_current_home_prob() -> None:
    elements = [
        {
            "data": {
                "id": "match:a",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Brazil vs. France",
                "home_team": "Brazil",
                "away_team": "France",
                "schedule_home": "Brazil",
                "schedule_away": "France",
                "match_end_epoch": 200,
                "winner_team": "France",
                "odds_series": [{"h": 100, "home": 0.3, "away": 0.7}],
            },
            "classes": "MATCH",
        }
    ]
    stamped = apply_time_slice(elements, 100)
    assert stamped[0]["data"]["current_home_prob"] == 0.3
    assert "30%" in stamped[0]["data"]["short_label"]
    locked = apply_time_slice(elements, 300)
    assert locked[0]["data"]["current_home_prob"] == 0.0
    assert "2026" not in format_hour_label(None)
    assert "UTC" in format_hour_label(1_783_200_000)
