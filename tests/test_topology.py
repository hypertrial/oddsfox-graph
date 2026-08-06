"""Tests for deterministic topology extraction."""

from __future__ import annotations

from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.infer import infer_event_fragments
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import SemanticMarket
from oddsgraph.topology import (
    classify_events,
    covered_event_ids,
    parse_group_winner_title,
    parse_match_title,
    parse_stage_elimination_title,
)

from tests.helpers import load_fixture_fragment, load_golden_markets


def _market(**kwargs) -> SemanticMarket:
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


def test_parse_match_title_basic_and_accented() -> None:
    assert parse_match_title("Brazil vs. Morocco") == ("Brazil", "Morocco", None)
    assert parse_match_title("Türkiye vs. Paraguay") == ("Türkiye", "Paraguay", None)
    assert parse_match_title("Germany vs. Côte d'Ivoire - More Markets") == (
        "Germany",
        "Côte d'Ivoire",
        "More Markets",
    )
    assert parse_match_title("World Cup Winner") is None


def test_parse_group_and_stage_titles() -> None:
    assert parse_group_winner_title("World Cup Group D Winner") == "D"
    assert parse_group_winner_title("World Cup Group L Winner") == "L"
    assert parse_group_winner_title("World Cup Winner") is None
    assert parse_stage_elimination_title("World Cup: Portugal Stage of Elimination") == (
        "Portugal"
    )
    assert parse_stage_elimination_title("World Cup: Cape Verde Stage of Elimination") == (
        "Cape Verde"
    )


def test_match_event_is_fully_covered() -> None:
    markets = [
        _market(market_id="10", sports_market_type="moneyline"),
        _market(
            market_id="11",
            sports_market_type="soccer_player_goals",
            question="Player: 1+ goals",
            group_item_title="Player: 1+ goals",
        ),
    ]
    results = classify_events(markets)
    assert results["100"].fully_covered is True
    types = {n.type for n in results["100"].fragment.nodes}
    assert NodeType.TEAM in types
    assert NodeType.MATCH in types
    assert NodeType.COMPETITION in types
    labels = {n.label for n in results["100"].fragment.nodes if n.type == NodeType.TEAM}
    assert labels == {"Brazil", "Morocco"}


def test_group_winner_emits_team_group_edges() -> None:
    markets = [
        _market(
            market_id="20",
            event_id="200",
            event_title="World Cup Group D Winner",
            event_slug="world-cup-group-d-winner",
            group_item_title="Czechia",
            question="Will Czechia win Group D?",
        ),
        _market(
            market_id="21",
            event_id="200",
            event_title="World Cup Group D Winner",
            event_slug="world-cup-group-d-winner",
            group_item_title="Australia",
            question="Will Australia win Group D?",
        ),
    ]
    result = classify_events(markets)["200"]
    assert result.fully_covered is True
    teams = {n.label for n in result.fragment.nodes if n.type == NodeType.TEAM}
    assert teams == {"Czechia", "Australia"}
    groups = [n for n in result.fragment.nodes if n.type == NodeType.GROUP]
    assert len(groups) == 1
    assert groups[0].label == "Group D"
    edge_types = {e.type for e in result.fragment.edges}
    assert EdgeType.PARTICIPATES_IN in edge_types
    assert EdgeType.PART_OF in edge_types


def test_stage_elimination_and_world_cup_winner() -> None:
    stage_markets = [
        _market(
            market_id="30",
            event_id="300",
            event_title="World Cup: Portugal Stage of Elimination",
            event_slug="portugal-stage",
            group_item_title="Final",
            question="Will Portugal be eliminated in the Final?",
        ),
        _market(
            market_id="31",
            event_id="300",
            event_title="World Cup: Portugal Stage of Elimination",
            event_slug="portugal-stage",
            group_item_title="Champion",
            question="Will Portugal win the World Cup?",
        ),
    ]
    stage = classify_events(stage_markets)["300"]
    assert stage.fully_covered is True
    assert any(n.type == NodeType.STAGE for n in stage.fragment.nodes)
    assert any(e.type == EdgeType.QUALIFIES_FOR for e in stage.fragment.edges)

    winner_markets = [
        _market(
            market_id="40",
            event_id="400",
            event_title="World Cup Winner",
            event_slug="world-cup-winner",
            group_item_title="Austria",
            question="Will Austria win the 2026 FIFA World Cup?",
        )
    ]
    winner = classify_events(winner_markets)["400"]
    assert winner.fully_covered is True
    assert any(n.label == "Austria" for n in winner.fragment.nodes)


def test_empty_group_winner_falls_through() -> None:
    markets = [
        _market(
            market_id="80",
            event_id="800",
            event_title="World Cup Group A Winner",
            event_slug="group-a",
            group_item_title=None,
            question="Will someone win Group A?",
        )
    ]
    result = classify_events(markets)["800"]
    assert result.fully_covered is False
    assert result.fragment.nodes == []


def test_unrecognized_title_falls_through() -> None:
    markets = [
        _market(
            market_id="50",
            event_id="500",
            event_title="World Cup: Golden Ball Winner",
            event_slug="golden-ball",
            group_item_title="Harry Kane",
            question="Will Harry Kane win the Golden Ball?",
        )
    ]
    result = classify_events(markets)["500"]
    assert result.fully_covered is False
    assert result.fragment.nodes == []
    assert covered_event_ids(markets) == set()


def test_match_uses_group_index_when_available() -> None:
    markets = [
        _market(
            market_id="60",
            event_id="g1",
            event_title="World Cup Group C Winner",
            event_slug="group-c",
            group_item_title="Brazil",
            question="Will Brazil win Group C?",
        ),
        _market(
            market_id="61",
            event_id="g1",
            event_title="World Cup Group C Winner",
            event_slug="group-c",
            group_item_title="Morocco",
            question="Will Morocco win Group C?",
        ),
        _market(
            market_id="62",
            event_id="m1",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            sports_market_type="moneyline",
        ),
    ]
    classified = classify_events(markets)
    match_nodes = classified["m1"].fragment.nodes
    assert any(n.type == NodeType.GROUP and n.label == "Group C" for n in match_nodes)


def test_infer_skips_llm_for_covered_events(tmp_path: Path) -> None:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.resume = False
    settings.deterministic_topology = True
    settings.ensure_dirs()

    markets = [_market(market_id="70", sports_market_type="moneyline")]
    results = infer_event_fragments(settings, markets, llm=None)
    assert results == {}
    report = settings.inference_report_path.read_text(encoding="utf-8")
    assert '"100": "deterministic"' in report


def test_golden_fixture_match_compatibility() -> None:
    markets = [m for m in load_golden_markets() if m.event_id == "351746"]
    assert markets
    settings = Settings()
    det = build_deterministic_fragments_by_event(markets)
    det_resolution = resolve_fragments(
        [det["351746"]], settings, inference_methods=["deterministic"]
    )

    fixture = load_fixture_fragment("351746")
    fixture_resolution = resolve_fragments(
        [fixture], settings, inference_methods=["llm"]
    )

    det_teams = {
        n.canonical_id: n.label
        for n in det_resolution.canonical_nodes.values()
        if n.type == NodeType.TEAM
    }
    fix_teams = {
        n.canonical_id: n.label
        for n in fixture_resolution.canonical_nodes.values()
        if n.type == NodeType.TEAM
    }
    assert det_teams == fix_teams

    det_matches = {
        n.canonical_id: n.label
        for n in det_resolution.canonical_nodes.values()
        if n.type == NodeType.MATCH
    }
    fix_matches = {
        n.canonical_id: n.label
        for n in fixture_resolution.canonical_nodes.values()
        if n.type == NodeType.MATCH
    }
    assert det_matches == fix_matches


def test_golden_fixture_group_winner_compatibility() -> None:
    markets = [m for m in load_golden_markets() if m.event_id == "98266"]
    assert markets
    settings = Settings()
    det = build_deterministic_fragments_by_event(markets)
    det_resolution = resolve_fragments(
        [det["98266"]], settings, inference_methods=["deterministic"]
    )

    groups = [
        n for n in det_resolution.canonical_nodes.values() if n.type == NodeType.GROUP
    ]
    assert len(groups) == 1
    assert groups[0].label == "Group D"
    assert groups[0].canonical_id == "group:world-cup-2026:group-d"

    teams = [
        n.label for n in det_resolution.canonical_nodes.values() if n.type == NodeType.TEAM
    ]
    assert len(teams) >= 1
