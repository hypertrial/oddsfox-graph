"""Deterministic graph fragment construction from semantic markets."""

from __future__ import annotations

from collections import defaultdict

from oddsfox_graph import ids
from oddsfox_graph.ontology import EdgeType, NodeType
from oddsfox_graph.schema import Edge, GraphFragment, Node, SemanticMarket


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
) -> dict[str, GraphFragment]:
    by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        by_event[market.event_id].append(market)

    return {
        event_id: build_deterministic_fragment(event_markets)
        for event_id, event_markets in by_event.items()
    }
