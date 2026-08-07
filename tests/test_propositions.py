"""Tests for deterministic proposition compilation."""

from __future__ import annotations

from oddsgraph import ids
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.propositions import compile_propositions, compile_propositions_by_event
from oddsgraph.schema import SemanticMarket


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


def test_match_moneylines_without_draw_skip_exactly_one() -> None:
    """Team-only moneylines must not claim an exclusive match-result partition."""
    markets = [
        _market(
            market_id="10",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            question="Will Brazil win on 2026-06-13?",
        ),
        _market(
            market_id="11",
            group_item_title="Morocco",
            sports_market_type="moneyline",
            question="Will Morocco win on 2026-06-13?",
        ),
    ]
    result = compile_propositions_by_event(markets)["100"]
    assert result.fully_covered is True
    assert len(result.propositions) == 4
    assert not any(e.type == EdgeType.EXACTLY_ONE for e in result.fragment.edges)
    assert not any(n.type == NodeType.CONSTRAINT for n in result.fragment.nodes)


def test_match_draw_plus_single_team_skips_exactly_one() -> None:
    """Draw + only one team moneyline is an incomplete winA/winB/draw partition."""
    brazil_draw = [
        _market(
            market_id="10",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            question="Will Brazil win on 2026-06-13?",
        ),
        _market(
            market_id="12",
            group_item_title="Draw (Brazil vs. Morocco)",
            sports_market_type="moneyline",
            question="Will Brazil vs. Morocco end in a draw?",
        ),
    ]
    morocco_draw = [
        _market(
            market_id="11",
            group_item_title="Morocco",
            sports_market_type="moneyline",
            question="Will Morocco win on 2026-06-13?",
        ),
        _market(
            market_id="12",
            group_item_title="Draw (Brazil vs. Morocco)",
            sports_market_type="moneyline",
            question="Will Brazil vs. Morocco end in a draw?",
        ),
    ]
    for markets in (brazil_draw, morocco_draw):
        result = compile_propositions_by_event(markets)["100"]
        assert not any(e.type == EdgeType.EXACTLY_ONE for e in result.fragment.edges)
        assert not any(n.type == NodeType.CONSTRAINT for n in result.fragment.nodes)


def test_match_moneyline_and_draw_compile_with_exactly_one() -> None:
    markets = [
        _market(
            market_id="10",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            question="Will Brazil win on 2026-06-13?",
        ),
        _market(
            market_id="11",
            group_item_title="Morocco",
            sports_market_type="moneyline",
            question="Will Morocco win on 2026-06-13?",
        ),
        _market(
            market_id="12",
            group_item_title="Draw (Brazil vs. Morocco)",
            sports_market_type="moneyline",
            question="Will Brazil vs. Morocco end in a draw?",
        ),
    ]
    result = compile_propositions_by_event(markets)["100"]
    assert result.fully_covered is True
    assert len(result.propositions) == 6

    yes_brazil = ids.outcome_id("10", "Yes")
    prop = result.propositions[yes_brazil]
    assert prop.predicate == "wins_match"
    assert prop.polarity is True
    assert prop.arguments["team"] == ids.team_id("Brazil")

    draw_yes = ids.outcome_id("12", "Yes")
    assert result.propositions[draw_yes].predicate == "draws_match"

    edge_types = {e.type for e in result.fragment.edges}
    assert EdgeType.PRICES in edge_types
    assert EdgeType.REFERS_TO in edge_types
    assert EdgeType.COMPLEMENT in edge_types
    assert EdgeType.EXACTLY_ONE in edge_types
    assert any(n.type == NodeType.CONSTRAINT for n in result.fragment.nodes)


def test_team_to_advance_categorical_outcomes() -> None:
    markets = [
        _market(
            market_id="13",
            event_title="Brazil vs. Morocco - More Markets",
            event_slug="fifwc-bra-mar-2026-06-13-more",
            sports_market_type="soccer_team_to_advance",
            group_item_title="Team to Advance",
            question="Brazil vs. Morocco: Team to Advance",
            outcomes=["Brazil", "Morocco"],
        )
    ]
    result = compile_propositions_by_event(markets)["100"]
    assert result.fully_covered is True
    brazil_id = ids.outcome_id("13", "Brazil")
    morocco_id = ids.outcome_id("13", "Morocco")
    assert result.propositions[brazil_id].predicate == "advances_match"
    assert result.propositions[brazil_id].polarity is True
    edge_types = {e.type for e in result.fragment.edges}
    assert EdgeType.EXACTLY_ONE in edge_types
    assert EdgeType.PRICES in edge_types
    assert EdgeType.REFERS_TO in edge_types
    exactly = [e for e in result.fragment.edges if e.type == EdgeType.EXACTLY_ONE]
    targets = {e.target for e in exactly}
    assert targets == {brazil_id, morocco_id}
    assert any(n.type == NodeType.CONSTRAINT for n in result.fragment.nodes)


def test_group_winner_and_world_cup_winner() -> None:
    group = [
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
    group_result = compile_propositions_by_event(group)["200"]
    assert group_result.fully_covered is True
    yes = ids.outcome_id("20", "Yes")
    assert group_result.propositions[yes].predicate == "wins_group"
    assert any(e.type == EdgeType.EXACTLY_ONE for e in group_result.fragment.edges)

    winner = [
        _market(
            market_id="40",
            event_id="400",
            event_title="World Cup Winner",
            event_slug="world-cup-winner",
            group_item_title="Austria",
            question="Will Austria win the 2026 FIFA World Cup?",
        ),
        _market(
            market_id="41",
            event_id="400",
            event_title="World Cup Winner",
            event_slug="world-cup-winner",
            group_item_title="Brazil",
            question="Will Brazil win the 2026 FIFA World Cup?",
        ),
    ]
    winner_result = compile_propositions_by_event(winner)["400"]
    assert winner_result.fully_covered is True
    assert winner_result.propositions[ids.outcome_id("40", "Yes")].predicate == (
        "wins_competition"
    )


def test_stage_elimination_exactly_one_partition() -> None:
    markets = [
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
        _market(
            market_id="32",
            event_id="300",
            event_title="World Cup: Portugal Stage of Elimination",
            event_slug="portugal-stage",
            group_item_title="Semifinals",
            question="Will Portugal be eliminated in the Semifinals?",
        ),
    ]
    result = compile_propositions_by_event(markets)["300"]
    assert result.fully_covered is True
    yes_final = ids.outcome_id("30", "Yes")
    prop = result.propositions[yes_final]
    assert prop.predicate == "eliminated_at_stage"
    assert "final" in prop.arguments["stage"]
    exactly = [e for e in result.fragment.edges if e.type == EdgeType.EXACTLY_ONE]
    assert len(exactly) == 3


def test_reaches_stage_templates() -> None:
    markets = [
        _market(
            market_id="50",
            event_id="500",
            event_title="World Cup: Nation to Reach Final",
            event_slug="nation-final",
            group_item_title="Ghana",
            question="Will Ghana reach the 2026 FIFA World Cup final?",
        ),
        _market(
            market_id="51",
            event_id="501",
            event_title="World Cup: Team to advance to Knockout Stages",
            event_slug="knockout",
            group_item_title="Uzbekistan",
            question="Will Uzbekistan advance to the knockout stages?",
        ),
    ]
    by_event = compile_propositions_by_event(markets)
    final = by_event["500"]
    assert final.fully_covered is True
    prop = final.propositions[ids.outcome_id("50", "Yes")]
    assert prop.predicate == "reaches_stage"
    assert "final" in prop.arguments["stage"]

    knockout = by_event["501"]
    assert knockout.fully_covered is True
    prop_k = knockout.propositions[ids.outcome_id("51", "Yes")]
    assert "round-of-32" in prop_k.arguments["stage"]


def test_unrecognized_event_left_uncompiled() -> None:
    markets = [
        _market(
            market_id="90",
            event_id="900",
            event_title="World Cup: Golden Ball Winner",
            event_slug="golden-ball",
            group_item_title="Harry Kane",
            question="Will Harry Kane win the Golden Ball?",
        )
    ]
    result = compile_propositions(markets)
    assert result.by_event["900"].fully_covered is False
    assert result.propositions == {}
