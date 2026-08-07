"""Tests for official WC2026 bracket topology."""

from __future__ import annotations

from collections import Counter

from oddsgraph.bracket import (
    ALL_STAGE_LABELS,
    STAGE_KEY_TO_LABEL,
    build_official_bracket_fragment,
    load_wc2026_schedule,
    tournament_time_bounds,
)
from oddsgraph.config import Settings
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import SemanticMarket
from oddsgraph.topology import classify_events


def _m(**kwargs) -> SemanticMarket:
    defaults = {
        "market_id": "1",
        "event_id": "100",
        "event_title": "Brazil vs. Morocco",
        "event_slug": "fifwc-bra-mar-2026-06-13",
        "question": "Will Brazil win?",
        "outcomes": ["Yes", "No"],
    }
    defaults.update(kwargs)
    return SemanticMarket(**defaults)


def test_tournament_playback_bounds_extend_through_final_full_time() -> None:
    from oddsgraph.bracket import (
        schedule_stage_windows,
        tournament_playback_bounds,
        tournament_time_bounds,
    )

    windows = schedule_stage_windows()
    assert [w.stage_key for w in windows] == [
        "group_stage",
        "round_of_32",
        "round_of_16",
        "quarterfinal",
        "semifinal",
        "third_place",
        "final",
    ]
    kickoff_start, kickoff_end = tournament_time_bounds()
    play_start, play_end = tournament_playback_bounds()
    r32 = next(w for w in windows if w.stage_key == "round_of_32")
    assert play_start == r32.start_hour
    assert play_start is not None and kickoff_start is not None
    assert play_start > kickoff_start
    assert play_end is not None and kickoff_end is not None
    assert play_end > kickoff_end
    assert play_end == windows[-1].end_hour
    assert windows[0].match_count == 72
    assert windows[-1].label == "Final"


def test_schedule_playback_milestones_are_kickoffs_and_full_times() -> None:
    from datetime import datetime, timezone

    from oddsgraph.bracket import (
        schedule_playback_milestones,
        tournament_playback_bounds,
    )

    milestones = schedule_playback_milestones()
    play_start, play_end = tournament_playback_bounds()
    assert len(milestones) == 64
    assert milestones == tuple(sorted(set(milestones)))
    assert milestones[0] == play_start
    assert milestones[-1] == play_end

    # Simultaneous knockout kickoffs collapse into one milestone epoch.
    # Group Stage kickoffs are excluded from playback.
    group_simultaneous = int(
        datetime(2026, 6, 24, 19, 0, tzinfo=timezone.utc).timestamp()
    )
    assert group_simultaneous not in milestones
    fixtures = [
        f
        for f in load_wc2026_schedule()["fixtures"]
        if f.get("kickoff_at_utc") == "2026-06-24T19:00:00"
    ]
    assert len(fixtures) == 2
    assert all(f.get("stage_key") == "group_stage" for f in fixtures)

def test_schedule_has_104_fixtures_with_expected_stage_counts() -> None:
    schedule = load_wc2026_schedule()
    fixtures = schedule["fixtures"]
    assert len(fixtures) == 104
    assert schedule["_provenance"]["row_count"] == 104
    counts = Counter(f["stage_key"] for f in fixtures)
    assert counts == {
        "group_stage": 72,
        "round_of_32": 16,
        "round_of_16": 8,
        "quarterfinal": 4,
        "semifinal": 2,
        "third_place": 1,
        "final": 1,
    }
    assert set(STAGE_KEY_TO_LABEL) == set(counts)


def test_tournament_time_bounds_span_first_to_last_kickoff() -> None:
    from datetime import datetime, timezone

    schedule = load_wc2026_schedule()
    kickoffs = [
        datetime.fromisoformat(str(f["kickoff_at_utc"])).replace(tzinfo=timezone.utc)
        for f in schedule["fixtures"]
    ]
    expected_start = int(min(kickoffs).timestamp())
    expected_end = int(max(kickoffs).timestamp())
    expected_start -= expected_start % 3600
    expected_end -= expected_end % 3600

    start, end = tournament_time_bounds()
    assert start == expected_start
    assert end == expected_end
    assert start == 1_781_204_400  # 2026-06-11 19:00 UTC
    assert end == 1_784_487_600  # 2026-07-19 19:00 UTC
    assert end > start
    assert start % 3600 == 0
    assert end % 3600 == 0


def test_schedule_knockout_outcomes_mark_spain_champion_and_england_third() -> None:
    from oddsgraph.bracket import schedule_knockout_outcomes

    outcomes = schedule_knockout_outcomes()
    final = outcomes["match:spain-vs-argentina-2026-07-19"]
    third = outcomes["match:france-vs-england-2026-07-18"]
    assert final["winner_team"] == "Spain"
    assert third["winner_team"] == "England"
    assert isinstance(final.get("match_end_epoch"), int)
    assert isinstance(third.get("match_end_epoch"), int)
    assert outcomes["match:france-vs-spain-2026-07-14"]["winner_team"] == "Spain"
    assert outcomes["match:england-vs-argentina-2026-07-15"]["winner_team"] == "Argentina"
    assert all(entry.get("winner_team") for entry in outcomes.values())
    assert all(
        isinstance(entry.get("match_end_epoch"), int) for entry in outcomes.values()
    )


def test_official_bracket_stage_ladder_and_match_placement() -> None:
    fragment = build_official_bracket_fragment()
    stages = {n.label for n in fragment.nodes if n.type == NodeType.STAGE}
    assert stages == set(ALL_STAGE_LABELS)

    matches = [n for n in fragment.nodes if n.type == NodeType.MATCH]
    assert len(matches) == 104

    stage_advances = [
        e
        for e in fragment.edges
        if e.type == EdgeType.ADVANCES_TO and e.source.startswith("stage:")
    ]
    assert len(stage_advances) == 7
    assert any(
        e.source.endswith(":semifinals") and e.target.endswith(":third-place")
        for e in stage_advances
    )
    assert any(
        e.source.endswith(":final") and e.target.endswith(":champion")
        for e in stage_advances
    )

    match_part_of_stage = [
        e
        for e in fragment.edges
        if e.type == EdgeType.PART_OF and e.source.startswith("match:")
    ]
    assert len(match_part_of_stage) == 104

    match_advances = [
        e
        for e in fragment.edges
        if e.type == EdgeType.ADVANCES_TO and e.source.startswith("match:")
    ]
    # 16 R32→R16 + 8 R16→QF + 4 QF→SF + 2 SF→Final + 2 SF→Third Place = 32
    assert len(match_advances) == 32


def test_semifinal_winner_and_loser_routing() -> None:
    fragment = build_official_bracket_fragment()
    match_by_label = {
        n.label: n.local_id for n in fragment.nodes if n.type == NodeType.MATCH
    }
    sf = match_by_label["France vs. Spain"]
    final = match_by_label["Spain vs. Argentina"]
    third = match_by_label["France vs. England"]

    advances = {
        (e.source, e.target)
        for e in fragment.edges
        if e.type == EdgeType.ADVANCES_TO and e.source.startswith("match:")
    }
    assert (sf, final) in advances  # Spain continues
    assert (sf, third) in advances  # France to third place


def test_alias_merge_korea_republic_with_bracket_south_korea() -> None:
    markets = [
        _m(
            market_id="g1",
            event_id="g1",
            event_title="World Cup Group A Winner",
            event_slug="group-a",
            group_item_title="South Korea",
            question="Will South Korea win Group A?",
        ),
        _m(
            market_id="g2",
            event_id="g1",
            event_title="World Cup Group A Winner",
            event_slug="group-a",
            group_item_title="Czechia",
            question="Will Czechia win Group A?",
        ),
        _m(
            market_id="m1",
            event_id="m1",
            event_title="Korea Republic vs. Czechia",
            event_slug="fifwc-kr-cze-2026-06-11",
            sports_market_type="moneyline",
        ),
    ]
    topo = classify_events(markets)["m1"]
    assert any(n.type == NodeType.GROUP and n.label == "Group A" for n in topo.fragment.nodes)
    teams = {n.label for n in topo.fragment.nodes if n.type == NodeType.TEAM}
    assert "South Korea" in teams
    assert "Korea Republic" not in teams
    korea = next(n for n in topo.fragment.nodes if n.type == NodeType.TEAM and n.label == "South Korea")
    assert "Curaçao" not in korea.aliases
    assert "kor" not in {a.casefold() for a in korea.aliases}

    settings = Settings()
    bracket = build_official_bracket_fragment()
    resolution = resolve_fragments(
        [topo.fragment, bracket],
        settings,
        inference_methods=["deterministic", "official_bracket"],
    )
    korea_nodes = [
        n
        for n in resolution.canonical_nodes.values()
        if n.type == NodeType.TEAM and "korea" in n.label.casefold()
    ]
    labels = {n.label for n in korea_nodes}
    assert labels == {"South Korea"}
