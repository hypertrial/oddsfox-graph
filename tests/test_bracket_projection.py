"""Tests for knockout bracket projection heuristics."""

from __future__ import annotations

import pytest

from oddsgraph.bracket_projection import (
    apply_bracket_projection,
    conditional_advance_score,
    normalize_pair,
    project_match_at_hour,
    reach_prob_for_rank,
    sparkline_points_for_side,
)
from oddsgraph.flags import flag_url_for_team, missing_flag_teams
from oddsgraph.bracket import load_wc2026_schedule
from oddsgraph import ids


def _series(*points: tuple[int, float]) -> list[dict]:
    return [{"h": h, "p": p} for h, p in points]


def test_normalize_and_conditional_ratio() -> None:
    home, away = normalize_pair(0.2, 0.6)
    assert home == 0.25
    assert away is not None and abs(away - 0.75) < 1e-9
    assert normalize_pair(None, 0.5) == (None, None)
    assert normalize_pair(0.0, 0.0) == (None, None)

    stage_odds = {
        "Brazil": {
            "Round of 16": _series((100, 0.8)),
            "Quarterfinals": _series((100, 0.4)),
        }
    }
    assert conditional_advance_score("Brazil", "Round of 16", 100, stage_odds) == 0.5
    assert reach_prob_for_rank("Brazil", "Round of 16", 100, stage_odds) == 0.8


def test_latest_reach_prob_uses_latest_at_or_before_hour() -> None:
    from oddsgraph.bracket_projection import latest_reach_prob, latest_series_point

    series = _series((10, 0.1), (20, 0.2), (30, 0.3), (40, 0.4))
    assert latest_reach_prob(series, 25) == 0.2
    assert latest_reach_prob(series, 30) == 0.3
    assert latest_reach_prob(series, 5) is None
    assert latest_reach_prob(series, None) == 0.1
    assert latest_series_point(series, 35, hour_key="h") == {"h": 30, "p": 0.3}


def test_sparkline_points_prefer_direct_odds_series() -> None:
    data = {
        "stage": "Round of 16",
        "odds_series": [
            {"h": 100, "home": 0.55, "away": 0.45},
            {"h": 200, "home": 0.60, "away": 0.40},
            {"h": 300, "home": 0.70, "away": 0.30},
        ],
    }
    stage_odds = {
        "France": {"Round of 16": _series((100, 0.9), (200, 0.85), (300, 0.8))},
    }
    home = sparkline_points_for_side(data, "France", "home", 200, stage_odds)
    away = sparkline_points_for_side(data, "Argentina", "away", 200, stage_odds)
    assert home == [(100, 0.55), (200, 0.60)]
    assert away == [(100, 0.45), (200, 0.40)]


def test_sparkline_points_fall_back_to_stage_reach() -> None:
    data = {"stage": "Round of 16", "odds_series": []}
    stage_odds = {
        "France": {
            "Round of 16": _series((100, 0.9), (200, 0.85), (300, 0.8)),
        },
    }
    points = sparkline_points_for_side(data, "France", "home", 200, stage_odds)
    assert points == [(100, 0.9), (200, 0.85)]


def test_sparkline_points_fall_back_when_direct_series_is_future_only() -> None:
    data = {
        "stage": "Round of 16",
        "odds_series": [
            {"h": 300, "home": 0.55, "away": 0.45},
            {"h": 400, "home": 0.60, "away": 0.40},
        ],
    }
    stage_odds = {
        "France": {"Round of 16": _series((100, 0.9), (200, 0.85))},
    }
    points = sparkline_points_for_side(data, "France", "home", 200, stage_odds)
    assert points == [(100, 0.9), (200, 0.85)]


def test_sparkline_points_lock_to_winner_after_full_time() -> None:
    data = {
        "stage": "Round of 32",
        "home_team": "Brazil",
        "away_team": "France",
        "winner_team": "France",
        "match_end_epoch": 200,
        "odds_series": [
            {"h": 100, "home": 0.55, "away": 0.45},
            {"h": 150, "home": 0.40, "away": 0.60},
        ],
    }
    home = sparkline_points_for_side(data, "Brazil", "home", 200, {})
    away = sparkline_points_for_side(data, "France", "away", 200, {})
    assert home[-1] == (200, 0.0)
    assert away[-1] == (200, 1.0)


def test_sparkline_points_empty_when_both_missing() -> None:
    data = {"stage": "Round of 16"}
    assert sparkline_points_for_side(data, "France", "home", 100, {}) == []
    assert sparkline_points_for_side(data, None, "home", 100, {}) == []


def test_sparkline_points_no_lookahead_past_hour() -> None:
    data = {
        "stage": "Quarterfinals",
        "odds_series": [
            {"h": 10, "home": 0.5, "away": 0.5},
            {"h": 20, "home": 0.6, "away": 0.4},
            {"h": 30, "home": 0.7, "away": 0.3},
        ],
    }
    assert sparkline_points_for_side(data, "A", "home", 15, {}) == [(10, 0.5)]
    # hour_epoch=None includes the full direct series (no scrub bound).
    assert sparkline_points_for_side(data, "A", "home", None, {}) == [
        (10, 0.5),
        (20, 0.6),
        (30, 0.7),
    ]


def test_format_prob_label_and_resolved_marks() -> None:
    from oddsgraph.bracket_projection import format_prob_label

    assert format_prob_label(1.0) == "100%"
    assert format_prob_label(0.0) == "0%"
    assert format_prob_label(0.4) == "40%"
    assert format_prob_label(None) == "—"


def test_apply_projection_flags_champion_and_third_place_winner() -> None:
    final_end = 1_784_494_800
    third_end = 1_784_415_600
    elements = [
        {
            "data": {
                "id": "final",
                "type": "MATCH",
                "stage": "Final",
                "label": "Spain vs. Argentina",
                "schedule_home": "Spain",
                "schedule_away": "Argentina",
                "home_team": "Spain",
                "away_team": "Argentina",
                "winner_team": "Spain",
                "match_start_epoch": final_end - 7200,
                "match_end_epoch": final_end,
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "third",
                "type": "MATCH",
                "stage": "Third Place",
                "label": "France vs. England",
                "schedule_home": "France",
                "schedule_away": "England",
                "home_team": "France",
                "away_team": "England",
                "winner_team": "England",
                "match_start_epoch": third_end - 7200,
                "match_end_epoch": third_end,
            },
            "classes": "MATCH",
        },
    ]
    out = apply_bracket_projection(elements, final_end, {})
    by_id = {el["data"]["id"]: el["data"] for el in out}
    assert by_id["final"]["is_champion"] is True
    assert by_id["final"]["resolved"] is True
    assert by_id["final"]["current_home_prob"] == 1.0
    assert by_id["third"]["is_third_place_winner"] is True
    assert by_id["third"]["resolved"] is True
    assert by_id["third"]["current_home_prob"] == 0.0

    before = apply_bracket_projection(elements, third_end - 1, {})
    before_by_id = {el["data"]["id"]: el["data"] for el in before}
    assert before_by_id["final"]["resolved"] is False
    assert before_by_id["third"]["resolved"] is False


def test_project_match_picks_branch_winners_and_normalizes() -> None:
    stage_odds = {
        "Brazil": {
            "Semifinals": _series((50, 0.7)),
            "Final": _series((50, 0.35)),
        },
        "France": {
            "Semifinals": _series((50, 0.4)),
            "Final": _series((50, 0.1)),
        },
        "Spain": {
            "Semifinals": _series((50, 0.6)),
            "Final": _series((50, 0.3)),
        },
        "Germany": {
            "Semifinals": _series((50, 0.55)),
            "Final": _series((50, 0.2)),
        },
    }
    feeders = [
        {
            "id": "qf1",
            "label": "Brazil vs. France",
            "schedule_home": "Brazil",
            "schedule_away": "France",
            "home_team": "Brazil",
            "away_team": "France",
            "stage": "Quarterfinals",
        },
        {
            "id": "qf2",
            "label": "Spain vs. Germany",
            "schedule_home": "Spain",
            "schedule_away": "Germany",
            "home_team": "Spain",
            "away_team": "Germany",
            "stage": "Quarterfinals",
        },
    ]
    projected = project_match_at_hour(
        {
            "id": "sf1",
            "stage": "Semifinals",
            "schedule_home": "TBD A",
            "schedule_away": "TBD B",
            "label": "TBD A vs. TBD B",
        },
        hour_epoch=50,
        predecessors=feeders,
        stage_odds=stage_odds,
    )
    assert projected.home.team == "Brazil"
    assert projected.away.team == "Spain"
    assert projected.projected is True
    assert projected.probability_available is True
    assert projected.current_home_prob is not None
    assert abs(projected.current_home_prob - 0.5) < 1e-9


def test_resolved_feeder_locks_winner_and_third_place_loser() -> None:
    stage_odds = {
        "England": {"Semifinals": _series((10, 0.9)), "Final": _series((10, 0.5))},
        "Argentina": {"Semifinals": _series((10, 0.8)), "Final": _series((10, 0.6))},
        "France": {"Semifinals": _series((10, 0.7)), "Final": _series((10, 0.2))},
        "Spain": {"Semifinals": _series((10, 0.75)), "Final": _series((10, 0.3))},
    }
    sf1 = {
        "id": "sf1",
        "schedule_home": "England",
        "schedule_away": "Argentina",
        "home_team": "England",
        "away_team": "Argentina",
        "winner_team": "Argentina",
        "match_end_epoch": 5,
        "stage": "Semifinals",
    }
    sf2 = {
        "id": "sf2",
        "schedule_home": "France",
        "schedule_away": "Spain",
        "home_team": "France",
        "away_team": "Spain",
        "winner_team": "France",
        "match_end_epoch": 5,
        "stage": "Semifinals",
    }
    third = project_match_at_hour(
        {
            "id": "third",
            "stage": "Third Place",
            "schedule_home": "France",
            "schedule_away": "England",
            "label": "France vs. England",
        },
        hour_epoch=10,
        predecessors=[sf1, sf2],
        stage_odds=stage_odds,
    )
    # Resolved losers; predecessors still listed SF1 then SF2 here.
    assert {third.home.team, third.away.team} == {"England", "Spain"}
    assert third.probability_available is False
    assert third.projection_method == "third_place_unavailable"


def test_unresolved_third_place_ranks_by_final_reach() -> None:
    from oddsgraph.bracket_projection import order_feeders_for_slots

    stage_odds = {
        "England": {"Final": _series((10, 0.55))},
        "Argentina": {"Final": _series((10, 0.70))},
        "France": {"Final": _series((10, 0.20))},
        "Spain": {"Final": _series((10, 0.40))},
    }
    sf_eng_arg = {
        "id": "sf1",
        "schedule_home": "England",
        "schedule_away": "Argentina",
        "home_team": "England",
        "away_team": "Argentina",
        "stage": "Semifinals",
    }
    sf_fra_esp = {
        "id": "sf2",
        "schedule_home": "France",
        "schedule_away": "Spain",
        "home_team": "France",
        "away_team": "Spain",
        "stage": "Semifinals",
    }
    ordered = order_feeders_for_slots(
        [sf_eng_arg, sf_fra_esp], "France", "England"
    )
    assert ordered[0]["id"] == "sf2"
    assert ordered[1]["id"] == "sf1"
    third = project_match_at_hour(
        {
            "id": "third",
            "stage": "Third Place",
            "schedule_home": "France",
            "schedule_away": "England",
            "label": "France vs. England",
        },
        hour_epoch=10,
        predecessors=ordered,
        stage_odds=stage_odds,
    )
    # Lower P(reach Final) in each SF branch.
    assert third.home.team == "France"
    assert third.away.team == "England"


def test_apply_bracket_projection_orders_by_stage() -> None:
    stage_odds = {
        "Brazil": {
            "Round of 16": _series((1, 0.9)),
            "Quarterfinals": _series((1, 0.45)),
        },
        "France": {
            "Round of 16": _series((1, 0.2)),
            "Quarterfinals": _series((1, 0.05)),
        },
        "Spain": {
            "Round of 16": _series((1, 0.8)),
            "Quarterfinals": _series((1, 0.4)),
        },
        "Germany": {
            "Round of 16": _series((1, 0.3)),
            "Quarterfinals": _series((1, 0.1)),
        },
    }
    elements = [
        {
            "data": {
                "id": "r16",
                "type": "MATCH",
                "stage": "Round of 16",
                "label": "Brazil vs. Spain",
                "schedule_home": "Brazil",
                "schedule_away": "Spain",
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r32a",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Brazil vs. France",
                "schedule_home": "Brazil",
                "schedule_away": "France",
                "home_team": "Brazil",
                "away_team": "France",
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r32b",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Spain vs. Germany",
                "schedule_home": "Spain",
                "schedule_away": "Germany",
                "home_team": "Spain",
                "away_team": "Germany",
            },
            "classes": "MATCH",
        },
        # Intentionally reverse edge insertion order vs schedule slots.
        {
            "data": {
                "id": "e2",
                "source": "r32b",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "e1",
                "source": "r32a",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    out = apply_bracket_projection(
        elements, 1, stage_odds, flag_url_for_team=flag_url_for_team
    )
    by_id = {el["data"]["id"]: el["data"] for el in out if "source" not in el["data"]}
    assert by_id["r16"]["home_team"] == "Brazil"
    assert by_id["r16"]["away_team"] == "Spain"
    assert by_id["r16"]["current_home_prob"] == pytest.approx(0.5)
    assert by_id["r16"]["flag_images"].count("/assets/flags/") == 2


def test_all_schedule_teams_have_local_flags() -> None:
    schedule = load_wc2026_schedule()
    teams = {
        ids.canonical_team_name(raw["home_team"])
        for raw in schedule.get("fixtures") or []
    } | {
        ids.canonical_team_name(raw["away_team"])
        for raw in schedule.get("fixtures") or []
    }
    assert missing_flag_teams(teams) == []


def test_flag_svgs_declare_explicit_root_dimensions() -> None:
    """Flag SVGs declare root width/height so browsers scale them reliably."""
    import re
    from pathlib import Path

    flags_dir = (
        Path(__file__).resolve().parents[1]
        / "oddsgraph"
        / "explorer"
        / "assets"
        / "flags"
    )
    svg_open = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
    missing: list[str] = []
    for path in sorted(flags_dir.glob("*.svg")):
        tag = svg_open.search(path.read_text(encoding="utf-8"))
        if tag is None:
            missing.append(f"{path.name}:no-svg")
            continue
        open_tag = tag.group(0)
        if "width=" not in open_tag or "height=" not in open_tag:
            missing.append(path.name)
    assert missing == []


def test_partial_stage_reach_falls_back_to_feeder_advance() -> None:
    """Incomplete stage-reach markets must not rank a single scored side."""
    from oddsgraph.bracket_projection import _pick_branch_team

    feeder = {
        "label": "Spain vs. France",
        "schedule_home": "Spain",
        "schedule_away": "France",
        "home_team": "Spain",
        "away_team": "France",
        "stage": "Semifinals",
        "odds_series": [{"h": 0, "home": 0.8, "away": 0.2}],
    }
    # Only France has Final reach; Spain is the clear advance favorite.
    stage_odds = {"France": {"Final": _series((0, 0.05))}}

    assert (
        _pick_branch_team(feeder, match_stage="Final", hour_epoch=100, stage_odds=stage_odds)
        == "Spain"
    )
    assert (
        _pick_branch_team(
            feeder,
            match_stage="Third Place",
            hour_epoch=100,
            stage_odds=stage_odds,
            prefer_loser=True,
        )
        == "France"
    )
    assert (
        _pick_branch_team(feeder, match_stage="Final", hour_epoch=100, stage_odds={})
        == "Spain"
    )


def test_projection_falls_back_to_feeder_advance_odds_without_stage_odds() -> None:
    """Without stage-reach series, use feeder advance odds — not schedule homes."""
    elements = [
        {
            "data": {
                "id": "r32a",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Brazil vs. France",
                "schedule_home": "Brazil",
                "schedule_away": "France",
                "home_team": "Brazil",
                "away_team": "France",
                "match_start_epoch": 1000,
                "match_end_epoch": None,
                "winner_team": None,
                "odds_series": [{"h": 1000, "home": 0.2, "away": 0.8}],
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r32b",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Spain vs. Germany",
                "schedule_home": "Spain",
                "schedule_away": "Germany",
                "home_team": "Spain",
                "away_team": "Germany",
                "match_start_epoch": 1000,
                "match_end_epoch": None,
                "winner_team": None,
                "odds_series": [{"h": 1000, "home": 0.1, "away": 0.9}],
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r16",
                "type": "MATCH",
                "stage": "Round of 16",
                "label": "Winner R32a vs Winner R32b",
                "schedule_home": "Brazil",
                "schedule_away": "Spain",
                "match_start_epoch": 5000,
                "match_end_epoch": None,
                "winner_team": None,
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "e1",
                "source": "r32a",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "e2",
                "source": "r32b",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    out = apply_bracket_projection(elements, 1000, {})
    r16 = next(el["data"] for el in out if el["data"].get("id") == "r16")
    assert r16["home_team"] == "France"
    assert r16["away_team"] == "Germany"
    assert r16.get("projected") is True

    # With neither stage odds nor advance series, do not invent schedule homes.
    bare = [
        {
            "data": {
                "id": "r32a",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Brazil vs. France",
                "schedule_home": "Brazil",
                "schedule_away": "France",
                "home_team": "Brazil",
                "away_team": "France",
                "match_start_epoch": 1000,
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r32b",
                "type": "MATCH",
                "stage": "Round of 32",
                "label": "Spain vs. Germany",
                "schedule_home": "Spain",
                "schedule_away": "Germany",
                "home_team": "Spain",
                "away_team": "Germany",
                "match_start_epoch": 1000,
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "r16",
                "type": "MATCH",
                "stage": "Round of 16",
                "label": "Winner R32a vs Winner R32b",
                "schedule_home": "Brazil",
                "schedule_away": "Spain",
                "match_start_epoch": 5000,
            },
            "classes": "MATCH",
        },
        {
            "data": {
                "id": "e1",
                "source": "r32a",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
        {
            "data": {
                "id": "e2",
                "source": "r32b",
                "target": "r16",
                "edge_type": "ADVANCES_TO",
            },
            "classes": "ADVANCES_TO",
        },
    ]
    out_bare = apply_bracket_projection(bare, 1000, {})
    r16_bare = next(el["data"] for el in out_bare if el["data"].get("id") == "r16")
    assert r16_bare.get("home_team") is None
    assert r16_bare.get("away_team") is None
    assert not r16_bare.get("projected")