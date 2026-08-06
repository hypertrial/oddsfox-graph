"""Deterministic graph fragment construction from semantic markets."""

from __future__ import annotations

from collections import defaultdict

from oddsgraph import ids
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node, SemanticMarket, merge_fragments
from oddsgraph.topology import DEFAULT_COMPETITION_LABEL, classify_events


def build_deterministic_fragment(markets: list[SemanticMarket]) -> GraphFragment:
    if not markets:
        return GraphFragment()

    event_id = markets[0].event_id
    event_label = markets[0].event_title or markets[0].event_slug or event_id
    event_market_ids = [m.market_id for m in markets]

    nodes: list[Node] = [
        Node(
            local_id=ids.event_id(event_id),
            type=NodeType.EVENT,
            label=event_label,
            aliases=[m.event_slug or "" for m in markets if m.event_slug],
            confidence=1.0,
            evidence_market_ids=event_market_ids,
        )
    ]
    edges: list[Edge] = []

    for market in markets:
        market_local_id = ids.market_id(market.market_id)
        market_label = market.question or market.market_slug or market.market_id

        nodes.append(
            Node(
                local_id=market_local_id,
                type=NodeType.MARKET,
                label=market_label,
                aliases=[market.market_slug or ""],
                confidence=1.0,
                evidence_market_ids=[market.market_id],
            )
        )
        edges.append(
            Edge(
                source=ids.event_id(event_id),
                target=market_local_id,
                type=EdgeType.HAS_MARKET,
                confidence=1.0,
                evidence_market_ids=[market.market_id],
                evidence_text=market.question or market.market_slug or "",
            )
        )

        for outcome_label in market.outcomes or []:
            outcome_local_id = ids.outcome_id(market.market_id, outcome_label)
            nodes.append(
                Node(
                    local_id=outcome_local_id,
                    type=NodeType.OUTCOME,
                    label=outcome_label,
                    confidence=1.0,
                    evidence_market_ids=[market.market_id],
                )
            )
            edges.append(
                Edge(
                    source=market_local_id,
                    target=outcome_local_id,
                    type=EdgeType.HAS_OUTCOME,
                    confidence=1.0,
                    evidence_market_ids=[market.market_id],
                    evidence_text=outcome_label,
                )
            )

    return GraphFragment(nodes=nodes, edges=edges)


def build_deterministic_fragments_by_event(
    markets: list[SemanticMarket],
    *,
    include_topology: bool = True,
    competition_label: str = DEFAULT_COMPETITION_LABEL,
    skip_topology_event_ids: set[str] | None = None,
) -> dict[str, GraphFragment]:
    by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        by_event[market.event_id].append(market)

    skip_topology = skip_topology_event_ids or set()
    topology_by_event = (
        classify_events(markets, competition_label=competition_label)
        if include_topology
        else {}
    )

    results: dict[str, GraphFragment] = {}
    for event_id, event_markets in by_event.items():
        base = build_deterministic_fragment(event_markets)
        if event_id in skip_topology:
            # Verified/corrected topology is supplied separately and must replace
            # template topology rather than union with it.
            results[event_id] = base
            continue
        topology = topology_by_event.get(event_id)
        if topology is not None and topology.fragment.nodes:
            results[event_id] = merge_fragments([base, topology.fragment])
        else:
            results[event_id] = base
    return results
