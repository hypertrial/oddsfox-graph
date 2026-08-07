"""Deterministic proposition compilation for OUTCOME nodes."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from oddsgraph import ids
from oddsgraph.fragments import make_edge, make_node, match_local_id
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node, Proposition, SemanticMarket, merge_fragments
from oddsgraph.topology import (
    DEFAULT_COMPETITION_LABEL,
    parse_group_winner_title,
    parse_match_title,
    parse_stage_elimination_title,
)

_DRAW_TITLE_RE = re.compile(r"^Draw\s*\(", re.IGNORECASE)
REACHES_STAGE_TITLES: dict[str, str] = {
    "world cup: nation to reach final": "Final",
    "world cup: nation to reach quarterfinals": "Quarterfinals",
    "world cup: nation to reach semifinals": "Semifinals",
    "world cup: nation to reach round of 16": "Round of 16",
    "world cup: team to advance to knockout stages": "Round of 32",
}
WORLD_CUP_WINNER_TITLE = "World Cup Winner"

# NodeType -> (local_id, label)
TargetMap = dict[NodeType, tuple[str, str]]


@dataclass
class EventPropositionResult:
    fragment: GraphFragment
    propositions: dict[str, Proposition] = field(default_factory=dict)
    fully_covered: bool = False


@dataclass
class PropositionCompilationResult:
    fragment: GraphFragment
    propositions: dict[str, Proposition] = field(default_factory=dict)
    by_event: dict[str, EventPropositionResult] = field(default_factory=dict)


def _date_from_slug(event_slug: str | None) -> str | None:
    if not event_slug:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", event_slug)
    return match.group(1) if match else None


def _match_local(team_a: str, team_b: str, event_slug: str | None) -> str:
    return match_local_id(team_a, team_b, date=_date_from_slug(event_slug))


def _is_yes(label: str) -> bool:
    return label.strip().casefold() == "yes"


def _is_no(label: str) -> bool:
    return label.strip().casefold() == "no"


def _binary_outcomes(market: SemanticMarket) -> tuple[str, str] | None:
    outcomes = [o for o in (market.outcomes or []) if o]
    yes = next((o for o in outcomes if _is_yes(o)), None)
    no = next((o for o in outcomes if _is_no(o)), None)
    if yes and no:
        return yes, no
    return None


def _edge(
    source: str,
    target: str,
    edge_type: EdgeType,
    evidence_market_ids: list[str],
    evidence_text: str = "",
) -> Edge:
    return make_edge(
        source,
        target,
        edge_type,
        evidence_market_ids,
        evidence_text=evidence_text,
        confidence=1.0,
    )


def _entity_node(
    local_id: str,
    node_type: NodeType,
    label: str,
    evidence: list[str],
) -> Node:
    return make_node(local_id, node_type, label, evidence, confidence=1.0)


def _constraint_node(local_id: str, label: str, evidence: list[str]) -> Node:
    return make_node(local_id, NodeType.CONSTRAINT, label, evidence, confidence=1.0)


def _refers_edges(
    outcome_id: str,
    targets: TargetMap,
    evidence: list[str],
    evidence_text: str,
) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for node_type, (target_id, label) in targets.items():
        nodes.append(_entity_node(target_id, node_type, label, evidence))
        edges.append(
            _edge(outcome_id, target_id, EdgeType.REFERS_TO, evidence, evidence_text)
        )
    return nodes, edges


def _prices_edge(market_id: str, outcome_id: str, evidence_text: str) -> Edge:
    return _edge(
        ids.market_id(market_id),
        outcome_id,
        EdgeType.PRICES,
        [market_id],
        evidence_text=evidence_text,
    )


def _complement_pair(
    market: SemanticMarket,
    yes_label: str,
    no_label: str,
    prop: Proposition,
    targets: TargetMap,
) -> tuple[dict[str, Proposition], list[Node], list[Edge]]:
    yes_id = ids.outcome_id(market.market_id, yes_label)
    no_id = ids.outcome_id(market.market_id, no_label)
    yes_prop = prop.model_copy(update={"polarity": True})
    no_prop = prop.model_copy(update={"polarity": False})
    evidence = [market.market_id]
    evidence_text = market.question or market.group_item_title or yes_label
    yes_nodes, yes_refers = _refers_edges(yes_id, targets, evidence, evidence_text)
    no_nodes, no_refers = _refers_edges(no_id, targets, evidence, evidence_text)
    edges = [
        _prices_edge(market.market_id, yes_id, evidence_text),
        _prices_edge(market.market_id, no_id, evidence_text),
        *yes_refers,
        *no_refers,
        _edge(yes_id, no_id, EdgeType.COMPLEMENT, evidence, evidence_text),
        _edge(no_id, yes_id, EdgeType.COMPLEMENT, evidence, evidence_text),
    ]
    return {yes_id: yes_prop, no_id: no_prop}, yes_nodes + no_nodes, edges


def _exactly_one(
    constraint_local: str,
    label: str,
    outcome_ids: list[str],
    evidence: list[str],
    evidence_text: str,
) -> tuple[list[Node], list[Edge]]:
    if len(outcome_ids) < 2:
        return [], []
    node = _constraint_node(constraint_local, label, evidence)
    edges = [
        _edge(constraint_local, oid, EdgeType.EXACTLY_ONE, evidence, evidence_text)
        for oid in outcome_ids
    ]
    return [node], edges


def _build_match_propositions(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventPropositionResult | None:
    title = markets[0].event_title
    parsed = parse_match_title(title)
    if not parsed:
        return None

    raw_a, raw_b, _category = parsed
    team_a = ids.canonical_team_name(raw_a)
    team_b = ids.canonical_team_name(raw_b)
    event_slug = markets[0].event_slug
    match_local = _match_local(team_a, team_b, event_slug)
    match_label = f"{team_a} vs. {team_b}"
    competition_local = ids.competition_id(competition_label)

    propositions: dict[str, Proposition] = {}
    edges: list[Edge] = []
    nodes: list[Node] = []
    partition_outcome_ids: list[str] = []
    partition_evidence: set[str] = set()
    partition_has_draw = False
    partition_team_wins = 0
    advance_outcome_ids: list[str] = []
    advance_evidence: set[str] = set()

    for market in markets:
        smt = market.sports_market_type or ""
        group_title = (market.group_item_title or "").strip()
        binary = _binary_outcomes(market)

        if smt == "moneyline" and binary and _DRAW_TITLE_RE.match(group_title):
            yes_label, no_label = binary
            prop = Proposition(
                predicate="draws_match",
                arguments={"match": match_local, "competition": competition_local},
            )
            targets: TargetMap = {
                NodeType.MATCH: (match_local, match_label),
                NodeType.COMPETITION: (competition_local, competition_label),
            }
            props, new_nodes, new_edges = _complement_pair(
                market, yes_label, no_label, prop, targets
            )
            propositions.update(props)
            nodes.extend(new_nodes)
            edges.extend(new_edges)
            partition_outcome_ids.append(ids.outcome_id(market.market_id, yes_label))
            partition_evidence.add(market.market_id)
            partition_has_draw = True
            continue

        if smt == "moneyline" and binary and group_title:
            team = ids.canonical_team_name(group_title)
            if ids.normalize_label(team) not in {
                ids.normalize_label(team_a),
                ids.normalize_label(team_b),
            }:
                continue
            yes_label, no_label = binary
            team_local = ids.team_id(team)
            prop = Proposition(
                predicate="wins_match",
                arguments={
                    "team": team_local,
                    "match": match_local,
                    "competition": competition_local,
                },
            )
            targets = {
                NodeType.TEAM: (team_local, team),
                NodeType.MATCH: (match_local, match_label),
                NodeType.COMPETITION: (competition_local, competition_label),
            }
            props, new_nodes, new_edges = _complement_pair(
                market, yes_label, no_label, prop, targets
            )
            propositions.update(props)
            nodes.extend(new_nodes)
            edges.extend(new_edges)
            partition_outcome_ids.append(ids.outcome_id(market.market_id, yes_label))
            partition_evidence.add(market.market_id)
            partition_team_wins += 1
            continue

        if smt == "soccer_team_to_advance":
            for outcome_label in market.outcomes or []:
                team = ids.canonical_team_name(outcome_label.strip())
                if ids.normalize_label(team) not in {
                    ids.normalize_label(team_a),
                    ids.normalize_label(team_b),
                }:
                    continue
                team_local = ids.team_id(team)
                outcome_local = ids.outcome_id(market.market_id, outcome_label)
                prop = Proposition(
                    predicate="advances_match",
                    arguments={
                        "team": team_local,
                        "match": match_local,
                        "competition": competition_local,
                    },
                )
                propositions[outcome_local] = prop
                advance_outcome_ids.append(outcome_local)
                advance_evidence.add(market.market_id)
                evidence = [market.market_id]
                evidence_text = market.question or outcome_label
                edges.append(_prices_edge(market.market_id, outcome_local, evidence_text))
                ref_nodes, ref_edges = _refers_edges(
                    outcome_local,
                    {
                        NodeType.TEAM: (team_local, team),
                        NodeType.MATCH: (match_local, match_label),
                        NodeType.COMPETITION: (competition_local, competition_label),
                    },
                    evidence,
                    evidence_text,
                )
                nodes.extend(ref_nodes)
                edges.extend(ref_edges)

    if not propositions:
        return EventPropositionResult(fragment=GraphFragment(), fully_covered=False)

    # Soccer match-result EXACTLY_ONE is winA/winB/draw. Without both team
    # moneylines and a draw market the partition is incomplete.
    if partition_has_draw and partition_team_wins >= 2:
        constraint_local = ids.constraint_id(
            "exact-match-result", competition_label, match_local
        )
        c_nodes, c_edges = _exactly_one(
            constraint_local,
            f"Exact result: {match_label}",
            partition_outcome_ids,
            sorted(partition_evidence),
            match_label,
        )
        nodes.extend(c_nodes)
        edges.extend(c_edges)

    if len(advance_outcome_ids) >= 2:
        constraint_local = ids.constraint_id(
            "exact-match-advance", competition_label, match_local
        )
        c_nodes, c_edges = _exactly_one(
            constraint_local,
            f"Team to advance: {match_label}",
            advance_outcome_ids,
            sorted(advance_evidence),
            match_label,
        )
        nodes.extend(c_nodes)
        edges.extend(c_edges)

    return EventPropositionResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        propositions=propositions,
        fully_covered=True,
    )


def _compile_single_subject_partition(
    markets: list[SemanticMarket],
    *,
    subject_fn: Callable[[SemanticMarket], str | None],
    make_prop: Callable[[str], tuple[Proposition, TargetMap]],
    constraint: tuple[str, str, str] | None = None,
) -> EventPropositionResult:
    """Compile binary Yes/No markets that share one subject dimension.

    ``subject_fn`` extracts the per-market subject label (team, stage, …).
    ``make_prop`` builds the proposition and REFERS_TO target map for that
    subject. When ``constraint`` is provided as
    ``(constraint_local_id, constraint_label, evidence_text)``, an
    ``EXACTLY_ONE`` partition is emitted over the Yes outcomes.
    """
    propositions: dict[str, Proposition] = {}
    edges: list[Edge] = []
    nodes: list[Node] = []
    yes_outcome_ids: list[str] = []
    evidence_ids: set[str] = set()

    for market in markets:
        binary = _binary_outcomes(market)
        subject = subject_fn(market)
        if not binary or not subject:
            continue
        yes_label, no_label = binary
        prop, targets = make_prop(subject)
        props, new_nodes, new_edges = _complement_pair(
            market, yes_label, no_label, prop, targets
        )
        propositions.update(props)
        nodes.extend(new_nodes)
        edges.extend(new_edges)
        yes_outcome_ids.append(ids.outcome_id(market.market_id, yes_label))
        evidence_ids.add(market.market_id)

    if not propositions:
        return EventPropositionResult(fragment=GraphFragment(), fully_covered=False)

    if constraint is not None and len(yes_outcome_ids) >= 2:
        constraint_local, constraint_label, evidence_text = constraint
        c_nodes, c_edges = _exactly_one(
            constraint_local,
            constraint_label,
            yes_outcome_ids,
            sorted(evidence_ids),
            evidence_text,
        )
        nodes.extend(c_nodes)
        edges.extend(c_edges)

    return EventPropositionResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        propositions=propositions,
        fully_covered=True,
    )


def _build_group_winner_propositions(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventPropositionResult | None:
    letter = parse_group_winner_title(markets[0].event_title)
    if not letter:
        return None

    group_label = f"Group {letter}"
    group_local = ids.group_id(competition_label, group_label)
    competition_local = ids.competition_id(competition_label)

    def subject_fn(market: SemanticMarket) -> str | None:
        raw = (market.group_item_title or "").strip()
        return raw or None

    def make_prop(raw_team: str) -> tuple[Proposition, TargetMap]:
        team = ids.canonical_team_name(raw_team)
        team_local = ids.team_id(team)
        prop = Proposition(
            predicate="wins_group",
            arguments={
                "team": team_local,
                "group": group_local,
                "competition": competition_local,
            },
        )
        targets: TargetMap = {
            NodeType.TEAM: (team_local, team),
            NodeType.GROUP: (group_local, group_label),
            NodeType.COMPETITION: (competition_local, competition_label),
        }
        return prop, targets

    return _compile_single_subject_partition(
        markets,
        subject_fn=subject_fn,
        make_prop=make_prop,
        constraint=(
            ids.constraint_id("exact-group-winner", competition_label, group_label),
            f"Exact winner: {group_label}",
            group_label,
        ),
    )


def _build_stage_elimination_propositions(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventPropositionResult | None:
    raw_team = parse_stage_elimination_title(markets[0].event_title)
    if not raw_team:
        return None

    team = ids.canonical_team_name(raw_team)
    team_local = ids.team_id(team)
    competition_local = ids.competition_id(competition_label)

    def subject_fn(market: SemanticMarket) -> str | None:
        stage_label = (market.group_item_title or "").strip()
        return stage_label or None

    def make_prop(stage_label: str) -> tuple[Proposition, TargetMap]:
        stage_local = ids.stage_id(competition_label, stage_label)
        prop = Proposition(
            predicate="eliminated_at_stage",
            arguments={
                "team": team_local,
                "competition": competition_local,
                "stage": stage_local,
            },
        )
        targets: TargetMap = {
            NodeType.TEAM: (team_local, team),
            NodeType.STAGE: (stage_local, stage_label),
            NodeType.COMPETITION: (competition_local, competition_label),
        }
        return prop, targets

    return _compile_single_subject_partition(
        markets,
        subject_fn=subject_fn,
        make_prop=make_prop,
        constraint=(
            ids.constraint_id("exact-elimination", competition_label, team),
            f"Exact elimination stage: {team}",
            f"{team} stage of elimination",
        ),
    )


def _build_world_cup_winner_propositions(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventPropositionResult | None:
    title = (markets[0].event_title or "").strip()
    if title.casefold() != WORLD_CUP_WINNER_TITLE.casefold():
        return None

    competition_local = ids.competition_id(competition_label)

    def subject_fn(market: SemanticMarket) -> str | None:
        raw_team = (market.group_item_title or "").strip()
        if not raw_team:
            question = market.question or ""
            q_match = re.match(r"^Will (.+?) win the .+ World Cup\?$", question)
            if q_match:
                raw_team = q_match.group(1).strip()
        return raw_team or None

    def make_prop(raw_team: str) -> tuple[Proposition, TargetMap]:
        team = ids.canonical_team_name(raw_team)
        team_local = ids.team_id(team)
        prop = Proposition(
            predicate="wins_competition",
            arguments={"team": team_local, "competition": competition_local},
        )
        targets: TargetMap = {
            NodeType.TEAM: (team_local, team),
            NodeType.COMPETITION: (competition_local, competition_label),
        }
        return prop, targets

    return _compile_single_subject_partition(
        markets,
        subject_fn=subject_fn,
        make_prop=make_prop,
        constraint=(
            ids.constraint_id("exact-champion", competition_label),
            f"Exact champion: {competition_label}",
            competition_label,
        ),
    )


def _parse_reaches_stage_title(title: str | None) -> str | None:
    if not title:
        return None
    return REACHES_STAGE_TITLES.get(title.strip().casefold())


def _build_reaches_stage_propositions(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventPropositionResult | None:
    stage_label = _parse_reaches_stage_title(markets[0].event_title)
    if not stage_label:
        return None

    stage_local = ids.stage_id(competition_label, stage_label)
    competition_local = ids.competition_id(competition_label)

    def subject_fn(market: SemanticMarket) -> str | None:
        raw_team = (market.group_item_title or "").strip()
        return raw_team or None

    def make_prop(raw_team: str) -> tuple[Proposition, TargetMap]:
        team = ids.canonical_team_name(raw_team)
        team_local = ids.team_id(team)
        prop = Proposition(
            predicate="reaches_stage",
            arguments={
                "team": team_local,
                "competition": competition_local,
                "stage": stage_local,
            },
        )
        targets: TargetMap = {
            NodeType.TEAM: (team_local, team),
            NodeType.STAGE: (stage_local, stage_label),
            NodeType.COMPETITION: (competition_local, competition_label),
        }
        return prop, targets

    return _compile_single_subject_partition(
        markets,
        subject_fn=subject_fn,
        make_prop=make_prop,
        constraint=None,
    )


def compile_propositions_by_event(
    markets: list[SemanticMarket],
    competition_label: str = DEFAULT_COMPETITION_LABEL,
) -> dict[str, EventPropositionResult]:
    """Compile propositions per event using deterministic market templates."""
    markets_by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        markets_by_event[market.event_id].append(market)

    results: dict[str, EventPropositionResult] = {}
    for event_id, event_markets in markets_by_event.items():
        result = (
            _build_match_propositions(event_markets, competition_label)
            or _build_group_winner_propositions(event_markets, competition_label)
            or _build_stage_elimination_propositions(event_markets, competition_label)
            or _build_world_cup_winner_propositions(event_markets, competition_label)
            or _build_reaches_stage_propositions(event_markets, competition_label)
        )
        if result is None:
            result = EventPropositionResult(
                fragment=GraphFragment(),
                fully_covered=False,
            )
        results[event_id] = result
    return results


def compile_propositions(
    markets: list[SemanticMarket],
    competition_label: str = DEFAULT_COMPETITION_LABEL,
) -> PropositionCompilationResult:
    """Compile all event propositions into one fragment + proposition map."""
    by_event = compile_propositions_by_event(
        markets, competition_label=competition_label
    )
    fragments = [
        result.fragment
        for result in by_event.values()
        if result.fragment.nodes or result.fragment.edges
    ]
    propositions: dict[str, Proposition] = {}
    for result in by_event.values():
        propositions.update(result.propositions)
    fragment = merge_fragments(fragments) if fragments else GraphFragment()
    return PropositionCompilationResult(
        fragment=fragment,
        propositions=propositions,
        by_event=by_event,
    )
