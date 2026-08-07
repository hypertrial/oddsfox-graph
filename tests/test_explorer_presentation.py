"""Tests for explorer presentation helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.bracket_projection import home_prob_at_hour
from oddsgraph.explorer.presentation import (
    apply_path_highlight,
    apply_time_slice,
    bracket_layout,
    bracket_positions,
    bracket_stage_headers,
    bracket_stylesheet,
    fifa_match_id,
    format_hour_label,
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
    assert "%" in str(node_style["background-width"])
    assert "%" in str(node_style["background-height"])
    assert "px" not in str(node_style["background-width"])
    assert "px" not in str(node_style["background-position-x"])
    header_style = next(
        rule["style"] for rule in styles if rule["selector"] == ".stage-header"
    )
    assert header_style["events"] == "no"
    assert header_style["label"] == "data(label)"
    assert "node[?resolved]" in selectors
    assert "node[current_home_prob]" not in selectors
    resolved_style = next(
        rule["style"] for rule in styles if rule["selector"] == "node[?resolved]"
    )
    assert resolved_style["background-color"] == "#0f2f28"
    assert resolved_style["border-color"] == "#14b8a6"
    champion_style = next(
        rule["style"] for rule in styles if rule["selector"] == "node[?is_champion]"
    )
    third_style = next(
        rule["style"]
        for rule in styles
        if rule["selector"] == "node[?is_third_place_winner]"
    )
    assert champion_style["border-color"] == "#14b8a6"
    assert champion_style["background-color"] == "#0f2f28"
    assert third_style["border-color"] == "#14b8a6"
    assert third_style["background-color"] == "#0f2f28"
    assert edge_style["target-arrow-shape"] == "none"
    assert header_style["width"] == node_style["width"]
    assert "node[timeline_state = \"active\"]" in selectors
    assert layout["padding"] >= 40


def test_format_hour_labels_compact_and_full() -> None:
    from oddsgraph.explorer.presentation import (
        format_hour_iso,
        format_hour_label_compact,
    )

    epoch = 1_783_200_000
    compact = format_hour_label_compact(epoch)
    full = format_hour_label(epoch)
    assert "·" in compact
    assert compact.endswith("UTC")
    assert "2026" not in compact or "Jun" in compact
    assert "UTC" in full
    assert "at" in full
    assert format_hour_iso(epoch).endswith("Z")
    assert format_hour_label(None) == "No odds history"
    assert format_hour_label_compact(None) == "No odds history"


def test_phase_at_hour_covers_groups_gaps_and_final_weekend() -> None:
    from oddsgraph.bracket import schedule_stage_windows
    from oddsgraph.explorer.presentation import phase_at_hour, tracker_step_states

    windows = {w.stage_key: w for w in schedule_stage_windows()}
    groups = phase_at_hour(windows["group_stage"].start_epoch)
    assert groups.state == "active"
    assert groups.tracker_step == "groups"
    assert "projected" in groups.detail.lower()

    r32 = phase_at_hour(windows["round_of_32"].start_epoch)
    assert r32.label == "Round of 32"
    assert r32.state == "active"

    before_qf = phase_at_hour(windows["quarterfinal"].start_epoch - 3600)
    assert before_qf.state == "intermission"
    assert before_qf.next_stage == "Quarterfinals"
    assert before_qf.tracker_step == "qf"

    third = phase_at_hour(windows["third_place"].start_epoch)
    assert third.tracker_step == "final_weekend"
    assert "Third" in third.detail

    final = phase_at_hour(windows["final"].start_epoch)
    assert final.tracker_step == "final_weekend"
    assert final.state == "active"

    complete = phase_at_hour(windows["final"].end_epoch)
    assert complete.state == "complete"
    steps = tracker_step_states(complete)
    assert steps[-1]["state"] == "completed"


def test_apply_time_slice_stamps_timeline_state() -> None:
    from oddsgraph.bracket import schedule_stage_windows
    from oddsgraph.explorer.presentation import bracket_stage_headers

    windows = {w.stage_key: w for w in schedule_stage_windows()}
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
            },
            "classes": "MATCH",
        },
        *bracket_stage_headers(columns={0, 2}),
    ]
    during_groups = apply_time_slice(elements, windows["group_stage"].start_epoch)
    by_id = {el["data"]["id"]: el["data"] for el in during_groups}
    assert by_id["match:a"]["timeline_state"] == "up-next"
    during_r32 = apply_time_slice(elements, windows["round_of_32"].start_epoch)
    by_id = {el["data"]["id"]: el["data"] for el in during_r32}
    assert by_id["match:a"]["timeline_state"] == "active"
    assert by_id["stage-header:0"]["timeline_state"] == "active"


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
    assert stamped[0]["data"]["resolved"] is False
    assert "30%" in stamped[0]["data"]["short_label"]
    locked = apply_time_slice(elements, 300)
    assert locked[0]["data"]["current_home_prob"] == 0.0
    assert locked[0]["data"]["resolved"] is True
    assert "2026" not in format_hour_label(None)
    assert "UTC" in format_hour_label(1_783_200_000)


def test_bracket_summary_text_omits_counts_without_elements() -> None:
    from oddsgraph.bracket import schedule_stage_windows
    from oddsgraph.explorer.presentation import bracket_summary_text

    windows = {w.stage_key: w for w in schedule_stage_windows()}
    hour = windows["final"].end_epoch
    plain = bracket_summary_text(None, hour)
    assert "Selected time" in plain
    assert "resolved matches" not in plain

    counted = bracket_summary_text(
        [
            {
                "data": {
                    "id": "m1",
                    "type": "MATCH",
                    "resolved": True,
                    "projected": False,
                },
                "classes": "MATCH",
            },
            {
                "data": {
                    "id": "m2",
                    "type": "MATCH",
                    "resolved": False,
                    "projected": True,
                },
                "classes": "MATCH",
            },
        ],
        hour,
    )
    assert "1 resolved matches, 1 projected." in counted


def test_time_slider_marks_include_playback_milestones() -> None:
    from oddsgraph.bracket import (
        schedule_playback_milestones,
        tournament_playback_bounds,
    )
    from oddsgraph.explorer.presentation import time_slider_marks

    min_hour, max_hour = tournament_playback_bounds()
    assert min_hour is not None and max_hour is not None
    marks = time_slider_marks(min_hour, max_hour)
    milestones = schedule_playback_milestones()
    assert len(marks) >= len(milestones)
    for epoch in milestones:
        assert epoch in marks
    assert isinstance(marks[min_hour], dict)
    assert marks[min_hour]["label"]
    unlabeled = [v for v in marks.values() if v == ""]
    assert unlabeled
    assert "Groups" in {
        v["label"] for v in marks.values() if isinstance(v, dict)
    }
