"""Tests for deterministic logical rules over propositions."""

from __future__ import annotations

from oddsgraph import ids
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.rules import apply_rules, stage_rank_for_proposition
from oddsgraph.schema import CanonicalEdge, CanonicalNode, Proposition


def _outcome(
    oid: str,
    prop: Proposition,
    evidence: list[str] | None = None,
) -> CanonicalNode:
    return CanonicalNode(
        canonical_id=oid,
        type=NodeType.OUTCOME,
        label=oid,
        confidence=1.0,
        evidence_market_ids=evidence or ["1"],
        proposition=prop,
    )


def test_stage_rank_from_stage_id() -> None:
    assert stage_rank_for_proposition("stage:world-cup-2026:final") > stage_rank_for_proposition(
        "stage:world-cup-2026:semifinals"
    )
    assert stage_rank_for_proposition("stage:world-cup-2026:round-of-32") == 1


def test_stage_monotonicity_and_champion_reaches_final() -> None:
    competition = ids.competition_id("World Cup 2026")
    team = ids.team_id("Brazil")
    final = ids.stage_id("World Cup 2026", "Final")
    semi = ids.stage_id("World Cup 2026", "Semifinals")

    nodes = [
        _outcome(
            "outcome:win",
            Proposition(
                predicate="wins_competition",
                arguments={"team": team, "competition": competition},
            ),
            ["w"],
        ),
        _outcome(
            "outcome:final",
            Proposition(
                predicate="reaches_stage",
                arguments={"team": team, "competition": competition, "stage": final},
            ),
            ["f"],
        ),
        _outcome(
            "outcome:semi",
            Proposition(
                predicate="reaches_stage",
                arguments={"team": team, "competition": competition, "stage": semi},
            ),
            ["s"],
        ),
    ]
    edges = apply_rules(nodes)
    implies = {(e.source_id, e.target_id, e.rule_id) for e in edges if e.edge_type == EdgeType.IMPLIES}
    assert ("outcome:win", "outcome:final", "wc.champion_reaches_final") in implies
    assert ("outcome:final", "outcome:semi", "wc.stage_monotonicity") in implies
    for edge in edges:
        assert edge.confidence == 1.0
        assert edge.derivation_type == "rule"
        assert edge.premises


def test_champion_does_not_imply_third_place() -> None:
    """Third Place shares Final's monotonic rank but is not the Final."""
    competition = ids.competition_id("World Cup 2026")
    team = ids.team_id("Brazil")
    third = ids.stage_id("World Cup 2026", "Third Place")
    final = ids.stage_id("World Cup 2026", "Final")
    nodes = [
        _outcome(
            "outcome:win",
            Proposition(
                predicate="wins_competition",
                arguments={"team": team, "competition": competition},
            ),
        ),
        _outcome(
            "outcome:third",
            Proposition(
                predicate="reaches_stage",
                arguments={"team": team, "competition": competition, "stage": third},
            ),
        ),
        _outcome(
            "outcome:final",
            Proposition(
                predicate="reaches_stage",
                arguments={"team": team, "competition": competition, "stage": final},
            ),
        ),
    ]
    edges = apply_rules(nodes)
    implies = {
        (e.source_id, e.target_id, e.rule_id)
        for e in edges
        if e.edge_type == EdgeType.IMPLIES
    }
    assert ("outcome:win", "outcome:final", "wc.champion_reaches_final") in implies
    assert ("outcome:win", "outcome:third", "wc.champion_reaches_final") not in implies


def test_champion_equivalent_to_elim_at_champion() -> None:
    competition = ids.competition_id("World Cup 2026")
    team = ids.team_id("Portugal")
    champion = ids.stage_id("World Cup 2026", "Champion")
    nodes = [
        _outcome(
            "outcome:win",
            Proposition(
                predicate="wins_competition",
                arguments={"team": team, "competition": competition},
            ),
        ),
        _outcome(
            "outcome:champ",
            Proposition(
                predicate="eliminated_at_stage",
                arguments={"team": team, "competition": competition, "stage": champion},
            ),
        ),
    ]
    edges = apply_rules(nodes)
    equiv = [e for e in edges if e.edge_type == EdgeType.EQUIVALENT]
    assert len(equiv) >= 2
    pairs = {(e.source_id, e.target_id) for e in equiv}
    assert ("outcome:win", "outcome:champ") in pairs
    assert ("outcome:champ", "outcome:win") in pairs


def test_single_match_winner_mutex() -> None:
    match = "match:brazil-vs-morocco-2026-06-13"
    competition = ids.competition_id("World Cup 2026")
    nodes = [
        _outcome(
            "outcome:bra",
            Proposition(
                predicate="wins_match",
                arguments={
                    "team": ids.team_id("Brazil"),
                    "match": match,
                    "competition": competition,
                },
            ),
        ),
        _outcome(
            "outcome:mar",
            Proposition(
                predicate="wins_match",
                arguments={
                    "team": ids.team_id("Morocco"),
                    "match": match,
                    "competition": competition,
                },
            ),
        ),
    ]
    edges = apply_rules(nodes)
    mutex = [e for e in edges if e.edge_type == EdgeType.MUTEX]
    assert len(mutex) == 2
    assert all(e.rule_id == "wc.single_match_winner_mutex" for e in mutex)


def test_elimination_implies_reaches_same_stage() -> None:
    competition = ids.competition_id("World Cup 2026")
    team = ids.team_id("Portugal")
    qf = ids.stage_id("World Cup 2026", "Quarterfinals")
    nodes = [
        _outcome(
            "outcome:elim",
            Proposition(
                predicate="eliminated_at_stage",
                arguments={"team": team, "competition": competition, "stage": qf},
            ),
        ),
        _outcome(
            "outcome:reach",
            Proposition(
                predicate="reaches_stage",
                arguments={"team": team, "competition": competition, "stage": qf},
            ),
        ),
    ]
    edges = apply_rules(nodes)
    implies = [e for e in edges if e.edge_type == EdgeType.IMPLIES]
    assert any(
        e.source_id == "outcome:elim"
        and e.target_id == "outcome:reach"
        and e.rule_id == "wc.elimination_implies_reaches"
        for e in implies
    )


def test_group_argument_does_not_change_rule_edge_set() -> None:
    """Competition/group args must not alter candidate generation or edges."""
    competition = ids.competition_id("World Cup 2026")
    final = ids.stage_id("World Cup 2026", "Final")
    semi = ids.stage_id("World Cup 2026", "Semifinals")
    group_a = ids.group_id("World Cup 2026", "Group A")

    base_nodes: list[CanonicalNode] = []
    with_group_nodes: list[CanonicalNode] = []
    for i in range(50):
        team = ids.team_id(f"Team {i}")
        win = Proposition(
            predicate="wins_competition",
            arguments={"team": team, "competition": competition},
        )
        reach_final = Proposition(
            predicate="reaches_stage",
            arguments={"team": team, "competition": competition, "stage": final},
        )
        reach_semi = Proposition(
            predicate="reaches_stage",
            arguments={"team": team, "competition": competition, "stage": semi},
        )
        base_nodes.extend(
            [
                _outcome(f"outcome:win:{i}", win),
                _outcome(f"outcome:final:{i}", reach_final),
                _outcome(f"outcome:semi:{i}", reach_semi),
            ]
        )
        with_group_nodes.extend(
            [
                _outcome(
                    f"outcome:win:{i}",
                    win.model_copy(
                        update={"arguments": {**win.arguments, "group": group_a}}
                    ),
                ),
                _outcome(
                    f"outcome:final:{i}",
                    reach_final.model_copy(
                        update={
                            "arguments": {**reach_final.arguments, "group": group_a}
                        }
                    ),
                ),
                _outcome(
                    f"outcome:semi:{i}",
                    reach_semi.model_copy(
                        update={
                            "arguments": {**reach_semi.arguments, "group": group_a}
                        }
                    ),
                ),
            ]
        )

    def _edge_key(edge: CanonicalEdge) -> tuple:
        return (edge.source_id, edge.target_id, edge.edge_type, edge.rule_id)

    base_edges = {_edge_key(e) for e in apply_rules(base_nodes)}
    group_edges = {_edge_key(e) for e in apply_rules(with_group_nodes)}
    assert base_edges == group_edges
    assert len(base_edges) > 0
