"""Deterministic topology extraction from structured Polymarket fields."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from oddsgraph import ids
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node, SemanticMarket

DEFAULT_COMPETITION_LABEL = "World Cup 2026"

_MATCH_TITLE_RE = re.compile(
    r"^(?P<a>.+?)\s+vs\.\s+(?P<b>.+?)(?:\s+-\s+(?P<category>.+))?$",
    re.IGNORECASE,
)
_GROUP_WINNER_RE = re.compile(
    r"^World Cup Group (?P<letter>[A-L]) Winner$",
    re.IGNORECASE,
)
_STAGE_ELIMINATION_RE = re.compile(
    r"^World Cup:\s+(?P<team>.+?)\s+Stage of Elimination$",
    re.IGNORECASE,
)
_WORLD_CUP_WINNER_TITLE = "World Cup Winner"
_SLUG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_SLUG_TEAM_CODES_RE = re.compile(
    r"^fifwc-(?P<code_a>[a-z]{2,3})-(?P<code_b>[a-z]{2,3})-",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EventTopologyResult:
    fragment: GraphFragment
    fully_covered: bool


@dataclass
class _TopologyIndex:
    team_to_group: dict[str, str] = field(default_factory=dict)
    group_evidence: dict[str, list[str]] = field(default_factory=dict)


def _is_player_prop(market: SemanticMarket) -> bool:
    smt = market.sports_market_type or ""
    return smt.startswith("soccer_player_")


def _competition_node(competition_label: str, evidence_market_ids: list[str]) -> Node:
    return Node(
        local_id=ids.competition_id(competition_label),
        type=NodeType.COMPETITION,
        label=competition_label,
        aliases=[ids.slugify(competition_label)],
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
    )


def _team_node(
    label: str,
    evidence_market_ids: list[str],
    aliases: list[str] | None = None,
) -> Node:
    return Node(
        local_id=ids.team_id(label),
        type=NodeType.TEAM,
        label=label,
        aliases=sorted({a for a in (aliases or []) if a}),
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
    )


def _group_node(
    competition_label: str,
    group_label: str,
    evidence_market_ids: list[str],
    aliases: list[str] | None = None,
) -> Node:
    return Node(
        local_id=ids.group_id(competition_label, group_label),
        type=NodeType.GROUP,
        label=group_label,
        aliases=sorted({a for a in (aliases or []) if a}),
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
    )


def _stage_node(
    competition_label: str,
    stage_label: str,
    evidence_market_ids: list[str],
) -> Node:
    return Node(
        local_id=ids.stage_id(competition_label, stage_label),
        type=NodeType.STAGE,
        label=stage_label,
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
    )


def _match_node(
    label: str,
    local_id: str,
    evidence_market_ids: list[str],
    aliases: list[str] | None = None,
) -> Node:
    return Node(
        local_id=local_id,
        type=NodeType.MATCH,
        label=label,
        aliases=sorted({a for a in (aliases or []) if a}),
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
    )


def _edge(
    source: str,
    target: str,
    edge_type: EdgeType,
    evidence_market_ids: list[str],
    evidence_text: str = "",
) -> Edge:
    return Edge(
        source=source,
        target=target,
        type=edge_type,
        confidence=1.0,
        evidence_market_ids=evidence_market_ids,
        evidence_text=evidence_text,
    )


def parse_match_title(title: str | None) -> tuple[str, str, str | None] | None:
    if not title:
        return None
    match = _MATCH_TITLE_RE.match(title.strip())
    if not match:
        return None
    team_a = match.group("a").strip()
    team_b = match.group("b").strip()
    category = match.group("category")
    if not team_a or not team_b:
        return None
    return team_a, team_b, category.strip() if category else None


def parse_group_winner_title(title: str | None) -> str | None:
    if not title:
        return None
    match = _GROUP_WINNER_RE.match(title.strip())
    if not match:
        return None
    return match.group("letter").upper()


def parse_stage_elimination_title(title: str | None) -> str | None:
    if not title:
        return None
    match = _STAGE_ELIMINATION_RE.match(title.strip())
    if not match:
        return None
    return match.group("team").strip()


def _date_from_slug(event_slug: str | None) -> str | None:
    if not event_slug:
        return None
    match = _SLUG_DATE_RE.search(event_slug)
    return match.group(1) if match else None


def _codes_from_slug(event_slug: str | None) -> tuple[str, str] | None:
    if not event_slug:
        return None
    match = _SLUG_TEAM_CODES_RE.match(event_slug)
    if not match:
        return None
    return match.group("code_a").lower(), match.group("code_b").lower()


def _match_local_id(team_a: str, team_b: str, event_slug: str | None) -> str:
    date = _date_from_slug(event_slug)
    slug_a = ids.slugify(team_a)
    slug_b = ids.slugify(team_b)
    if date:
        return ids.match_id(f"{slug_a}-vs-{slug_b}-{date}")
    return ids.match_id(f"{slug_a}-vs-{slug_b}")


def _match_alias(event_slug: str | None) -> str | None:
    if not event_slug:
        return None
    # Prefer the base match slug without category suffix.
    date = _date_from_slug(event_slug)
    codes = _codes_from_slug(event_slug)
    if codes and date:
        return f"fifwc-{codes[0]}-{codes[1]}-{date}"
    return event_slug


def _code_aliases_for_team(code: str, team_label: str) -> list[str]:
    """Return slug-code aliases that belong to ``team_label`` only.

    Polymarket WC2026 reuses some FIFA-looking codes incorrectly (e.g. ``kor``
    for Curaçao). Always keep the raw code as a weak alias, but only attach the
    mapped display name when it canonicalizes to this team.
    """
    aliases = [code]
    mapped = ids.load_team_codes().get(code.lower())
    if not mapped:
        return aliases
    if ids.normalize_label(ids.canonical_team_name(mapped)) == ids.normalize_label(
        team_label
    ):
        aliases.extend(ids.team_aliases_from_code(code))
    return aliases


def _build_group_index(markets_by_event: dict[str, list[SemanticMarket]]) -> _TopologyIndex:
    index = _TopologyIndex()
    for event_markets in markets_by_event.values():
        title = event_markets[0].event_title
        letter = parse_group_winner_title(title)
        if not letter:
            continue
        group_label = f"Group {letter}"
        for market in event_markets:
            raw_team = (market.group_item_title or "").strip()
            if not raw_team:
                continue
            team = ids.canonical_team_name(raw_team)
            key = ids.normalize_label(team)
            index.team_to_group[key] = group_label
            index.group_evidence.setdefault(key, []).append(market.market_id)
    return index


def _build_match_fragment(
    markets: list[SemanticMarket],
    competition_label: str,
    group_index: _TopologyIndex,
) -> EventTopologyResult | None:
    title = markets[0].event_title
    parsed = parse_match_title(title)
    if not parsed:
        return None

    raw_team_a, raw_team_b, _category = parsed
    team_a = ids.canonical_team_name(raw_team_a)
    team_b = ids.canonical_team_name(raw_team_b)
    evidence = [m.market_id for m in markets]
    event_slug = markets[0].event_slug
    match_label = f"{team_a} vs. {team_b}"
    match_local = _match_local_id(team_a, team_b, event_slug)
    match_alias = _match_alias(event_slug)

    aliases_a: list[str] = []
    aliases_b: list[str] = []
    if raw_team_a != team_a:
        aliases_a.append(raw_team_a)
    if raw_team_b != team_b:
        aliases_b.append(raw_team_b)
    codes = _codes_from_slug(event_slug)
    if codes:
        aliases_a.extend(_code_aliases_for_team(codes[0], team_a))
        aliases_b.extend(_code_aliases_for_team(codes[1], team_b))

    nodes: list[Node] = [
        _competition_node(competition_label, evidence),
        _team_node(team_a, evidence, aliases=aliases_a),
        _team_node(team_b, evidence, aliases=aliases_b),
        _match_node(
            match_label,
            match_local,
            evidence,
            aliases=[match_alias] if match_alias else None,
        ),
    ]
    edges: list[Edge] = [
        _edge(
            ids.team_id(team_a),
            match_local,
            EdgeType.PARTICIPATES_IN,
            evidence,
            evidence_text=match_label,
        ),
        _edge(
            ids.team_id(team_b),
            match_local,
            EdgeType.PARTICIPATES_IN,
            evidence,
            evidence_text=match_label,
        ),
    ]

    group_a = group_index.team_to_group.get(ids.normalize_label(team_a))
    group_b = group_index.team_to_group.get(ids.normalize_label(team_b))
    if group_a and group_a == group_b:
        group_evidence = sorted(
            set(group_index.group_evidence.get(ids.normalize_label(team_a), []))
            | set(group_index.group_evidence.get(ids.normalize_label(team_b), []))
            | set(evidence)
        )
        group_node = _group_node(competition_label, group_a, group_evidence)
        nodes.append(group_node)
        edges.extend(
            [
                _edge(
                    group_node.local_id,
                    ids.competition_id(competition_label),
                    EdgeType.PART_OF,
                    group_evidence,
                    evidence_text=group_a,
                ),
                _edge(
                    match_local,
                    group_node.local_id,
                    EdgeType.PART_OF,
                    group_evidence,
                    evidence_text=group_a,
                ),
                _edge(
                    ids.team_id(team_a),
                    group_node.local_id,
                    EdgeType.PARTICIPATES_IN,
                    group_evidence,
                    evidence_text=group_a,
                ),
                _edge(
                    ids.team_id(team_b),
                    group_node.local_id,
                    EdgeType.PARTICIPATES_IN,
                    group_evidence,
                    evidence_text=group_a,
                ),
            ]
        )

    return EventTopologyResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        fully_covered=True,
    )


def _build_group_winner_fragment(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventTopologyResult | None:
    title = markets[0].event_title
    letter = parse_group_winner_title(title)
    if not letter:
        return None

    group_label = f"Group {letter}"
    evidence = [m.market_id for m in markets]
    nodes: list[Node] = [
        _competition_node(competition_label, evidence),
        _group_node(
            competition_label,
            group_label,
            evidence,
            aliases=[markets[0].event_slug] if markets[0].event_slug else None,
        ),
    ]
    edges: list[Edge] = [
        _edge(
            ids.group_id(competition_label, group_label),
            ids.competition_id(competition_label),
            EdgeType.PART_OF,
            evidence,
            evidence_text=title or group_label,
        )
    ]

    for market in markets:
        raw_team = (market.group_item_title or "").strip()
        if not raw_team:
            continue
        team = ids.canonical_team_name(raw_team)
        team_evidence = [market.market_id]
        aliases = [raw_team] if raw_team != team else None
        nodes.append(_team_node(team, team_evidence, aliases=aliases))
        edges.append(
            _edge(
                ids.team_id(team),
                ids.group_id(competition_label, group_label),
                EdgeType.PARTICIPATES_IN,
                team_evidence,
                evidence_text=market.question or team,
            )
        )

    has_teams = any(n.type == NodeType.TEAM for n in nodes)
    if not has_teams:
        return EventTopologyResult(
            fragment=GraphFragment(),
            fully_covered=False,
        )

    return EventTopologyResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        fully_covered=True,
    )


def _build_stage_elimination_fragment(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventTopologyResult | None:
    title = markets[0].event_title
    raw_team = parse_stage_elimination_title(title)
    if not raw_team:
        return None
    team = ids.canonical_team_name(raw_team)

    evidence = [m.market_id for m in markets]
    team_aliases = [raw_team] if raw_team != team else None
    nodes: list[Node] = [
        _competition_node(competition_label, evidence),
        _team_node(team, evidence, aliases=team_aliases),
    ]
    edges: list[Edge] = []
    stages_seen: set[str] = set()

    for market in markets:
        stage_label = (market.group_item_title or "").strip()
        if not stage_label:
            continue
        stage_id = ids.stage_id(competition_label, stage_label)
        if stage_label not in stages_seen:
            nodes.append(_stage_node(competition_label, stage_label, [market.market_id]))
            edges.append(
                _edge(
                    stage_id,
                    ids.competition_id(competition_label),
                    EdgeType.PART_OF,
                    [market.market_id],
                    evidence_text=stage_label,
                )
            )
            stages_seen.add(stage_label)
        edges.append(
            _edge(
                ids.team_id(team),
                stage_id,
                EdgeType.QUALIFIES_FOR,
                [market.market_id],
                evidence_text=market.question or stage_label,
            )
        )

    if not stages_seen:
        return EventTopologyResult(
            fragment=GraphFragment(),
            fully_covered=False,
        )

    return EventTopologyResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        fully_covered=True,
    )


def _build_world_cup_winner_fragment(
    markets: list[SemanticMarket],
    competition_label: str,
) -> EventTopologyResult | None:
    title = (markets[0].event_title or "").strip()
    if title.casefold() != _WORLD_CUP_WINNER_TITLE.casefold():
        return None

    evidence = [m.market_id for m in markets]
    stage_label = "Champion"
    nodes: list[Node] = [
        _competition_node(competition_label, evidence),
        _stage_node(competition_label, stage_label, evidence),
    ]
    edges: list[Edge] = [
        _edge(
            ids.stage_id(competition_label, stage_label),
            ids.competition_id(competition_label),
            EdgeType.PART_OF,
            evidence,
            evidence_text=stage_label,
        )
    ]

    for market in markets:
        raw_team = (market.group_item_title or "").strip()
        if not raw_team:
            # Fall back to question patterns like "Will Austria win..."
            question = market.question or ""
            q_match = re.match(r"^Will (.+?) win the .+ World Cup\?$", question)
            if q_match:
                raw_team = q_match.group(1).strip()
        if not raw_team:
            continue
        team = ids.canonical_team_name(raw_team)
        aliases = [raw_team] if raw_team != team else None
        nodes.append(_team_node(team, [market.market_id], aliases=aliases))
        edges.append(
            _edge(
                ids.team_id(team),
                ids.stage_id(competition_label, stage_label),
                EdgeType.QUALIFIES_FOR,
                [market.market_id],
                evidence_text=market.question or team,
            )
        )

    has_teams = any(n.type == NodeType.TEAM for n in nodes)
    if not has_teams:
        return EventTopologyResult(
            fragment=GraphFragment(),
            fully_covered=False,
        )

    return EventTopologyResult(
        fragment=GraphFragment(nodes=nodes, edges=edges),
        fully_covered=True,
    )


def classify_events(
    markets: list[SemanticMarket],
    competition_label: str = DEFAULT_COMPETITION_LABEL,
) -> dict[str, EventTopologyResult]:
    """Classify events and build deterministic topology fragments.

    Player-prop markets are ignored for topology signal beyond the shared event
    title (match pairing). Unrecognized events return an empty fragment with
    ``fully_covered=False`` so the LLM path remains the fallback.
    """
    markets_by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        markets_by_event[market.event_id].append(market)

    group_index = _build_group_index(markets_by_event)
    results: dict[str, EventTopologyResult] = {}

    for event_id, event_markets in markets_by_event.items():
        # Prefer non-player-prop markets for evidence when available, but titles
        # are shared so player-prop-only events still parse from the title.
        topology_markets = [m for m in event_markets if not _is_player_prop(m)]
        if not topology_markets:
            topology_markets = event_markets

        result = (
            _build_match_fragment(topology_markets, competition_label, group_index)
            or _build_group_winner_fragment(topology_markets, competition_label)
            or _build_stage_elimination_fragment(topology_markets, competition_label)
            or _build_world_cup_winner_fragment(topology_markets, competition_label)
        )
        if result is None:
            result = EventTopologyResult(
                fragment=GraphFragment(),
                fully_covered=False,
            )
        results[event_id] = result

    return results


def covered_event_ids(
    markets: list[SemanticMarket],
    competition_label: str = DEFAULT_COMPETITION_LABEL,
) -> set[str]:
    classified = classify_events(markets, competition_label=competition_label)
    return {eid for eid, result in classified.items() if result.fully_covered}
