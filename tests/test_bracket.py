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
