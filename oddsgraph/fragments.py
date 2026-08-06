"""Shared fragment node/edge constructors for topology and bracket builders."""

from __future__ import annotations

from oddsgraph import ids
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, Node


def make_node(
    local_id: str,
    node_type: NodeType,
    label: str,
    evidence_market_ids: list[str],
    aliases: list[str] | None = None,
    confidence: float = 1.0,
) -> Node:
    return Node(
        local_id=local_id,
        type=node_type,
        label=label,
        aliases=sorted({a for a in (aliases or []) if a}),
        confidence=confidence,
        evidence_market_ids=evidence_market_ids,
    )


def make_edge(
    source: str,
    target: str,
    edge_type: EdgeType,
    evidence_market_ids: list[str],
    evidence_text: str = "",
    confidence: float = 1.0,
) -> Edge:
    return Edge(
        source=source,
        target=target,
        type=edge_type,
        confidence=confidence,
        evidence_market_ids=evidence_market_ids,
        evidence_text=evidence_text,
    )


def match_local_id(team_a: str, team_b: str, date: str | None = None) -> str:
    """Build a MATCH local_id from team labels and optional YYYY-MM-DD date."""
    slug_a = ids.slugify(team_a)
    slug_b = ids.slugify(team_b)
    if date:
        return ids.match_id(f"{slug_a}-vs-{slug_b}-{date}")
    return ids.match_id(f"{slug_a}-vs-{slug_b}")
