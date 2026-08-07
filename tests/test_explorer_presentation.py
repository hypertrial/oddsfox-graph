"""Tests for explorer presentation helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.bracket_projection import home_prob_at_hour
from oddsgraph.explorer.bracket_view import short_match_label, stage_rank
from oddsgraph.explorer.presentation import (
    apply_time_slice,
    fifa_match_id,
    format_hour_label,
    stamp_odds_motion,
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
    # Playback skips Groups; phase still resolves but tracker maps to R32.
    assert groups.tracker_step == "r32"
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
    assert [s["id"] for s in steps] == [
        "r32",
        "r16",
        "qf",
        "sf",
        "final_weekend",
    ]

def test_apply_time_slice_stamps_timeline_state() -> None:
    from oddsgraph.bracket import schedule_stage_windows

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
    ]
    during_groups = apply_time_slice(elements, windows["group_stage"].start_epoch)
    by_id = {el["data"]["id"]: el["data"] for el in during_groups}
    assert by_id["match:a"]["timeline_state"] == "up-next"
    during_r32 = apply_time_slice(elements, windows["round_of_32"].start_epoch)
    by_id = {el["data"]["id"]: el["data"] for el in during_r32}
    assert by_id["match:a"]["timeline_state"] == "active"


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


def test_stamp_odds_motion_marks_ticks_and_favorite_flip() -> None:
    previous = [
        {
            "data": {
                "id": "match:a",
                "type": "MATCH",
                "home_team": "Brazil",
                "away_team": "France",
                "current_home_prob": 0.62,
                "resolved": False,
            },
            "classes": "MATCH",
        }
    ]
    current = [
        {
            "data": {
                "id": "match:a",
                "type": "MATCH",
                "home_team": "Brazil",
                "away_team": "France",
                "current_home_prob": 0.47,
                "resolved": False,
            },
            "classes": "MATCH",
        }
    ]
    stamped = stamp_odds_motion(current, previous)
    data = stamped[0]["data"]
    assert data["home_prob_delta_pp"] == -15
    assert data["odds_tick_home"] == "down"
    assert data["odds_tick_away"] == "up"
    assert data["favorite_flipped"] is True

    # Team swap should not invent a delta on the new pairing.
    swapped = [
        {
            "data": {
                "id": "match:a",
                "type": "MATCH",
                "home_team": "Spain",
                "away_team": "Germany",
                "current_home_prob": 0.8,
                "resolved": False,
            },
            "classes": "MATCH",
        }
    ]
    quiet = stamp_odds_motion(swapped, previous)
    assert "odds_tick_home" not in quiet[0]["data"]


def test_apply_time_slice_stamps_odds_motion_vs_previous() -> None:
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
                "odds_series": [
                    {"h": 100, "home": 0.4, "away": 0.6},
                    {"h": 200, "home": 0.7, "away": 0.3},
                ],
            },
            "classes": "MATCH",
        }
    ]
    earlier = apply_time_slice(elements, 100)
    later = apply_time_slice(elements, 200, previous_elements=earlier)
    data = later[0]["data"]
    assert data["current_home_prob"] == 0.7
    assert data["home_prob_delta_pp"] == 30
    assert data["odds_tick_home"] == "up"
    assert data["favorite_flipped"] is True


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
    assert stamped[0]["data"]["just_finished"] is False
    assert "30%" in stamped[0]["data"]["short_label"]
    at_full_time = apply_time_slice(elements, 200)
    assert at_full_time[0]["data"]["resolved"] is True
    assert at_full_time[0]["data"]["just_finished"] is True
    locked = apply_time_slice(elements, 300)
    assert locked[0]["data"]["current_home_prob"] == 0.0
    assert locked[0]["data"]["resolved"] is True
    assert locked[0]["data"]["just_finished"] is False
    assert "2026" not in format_hour_label(None)
    assert "UTC" in format_hour_label(1_783_200_000)


def test_apply_time_slice_marks_simultaneous_full_times() -> None:
    elements = [
        {
            "data": {
                "id": "match:a",
                "type": "MATCH",
                "stage": "Round of 32",
                "home_team": "Brazil",
                "away_team": "France",
                "schedule_home": "Brazil",
                "schedule_away": "France",
                "match_end_epoch": 200,
                "winner_team": "Brazil",
                "odds_series": [{"h": 100, "home": 0.6, "away": 0.4}],
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "match:b",
                "type": "MATCH",
                "stage": "Round of 32",
                "home_team": "Spain",
                "away_team": "Germany",
                "schedule_home": "Spain",
                "schedule_away": "Germany",
                "match_end_epoch": 200,
                "winner_team": "Spain",
                "odds_series": [{"h": 100, "home": 0.55, "away": 0.45}],
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "match:c",
                "type": "MATCH",
                "stage": "Round of 32",
                "home_team": "England",
                "away_team": "Portugal",
                "schedule_home": "England",
                "schedule_away": "Portugal",
                "match_end_epoch": 400,
                "winner_team": "England",
                "odds_series": [{"h": 100, "home": 0.5, "away": 0.5}],
            },
            "classes": "MATCH",
        },
    ]
    stamped = apply_time_slice(elements, 200)
    by_id = {el["data"]["id"]: el["data"] for el in stamped}
    assert by_id["match:a"]["just_finished"] is True
    assert by_id["match:b"]["just_finished"] is True
    assert by_id["match:c"]["just_finished"] is False
    assert by_id["match:c"]["resolved"] is False


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
    labels = {v["label"] for v in marks.values() if isinstance(v, dict)}
    assert "R32" in labels
    assert "Groups" not in labels
