"""Graph ontology: node types, edge types, and allowed patterns."""

from __future__ import annotations

from enum import Enum
from typing import Any


class NodeType(str, Enum):
    COMPETITION = "COMPETITION"
    STAGE = "STAGE"
    GROUP = "GROUP"
    ROUND = "ROUND"
    MATCH = "MATCH"
    TEAM = "TEAM"
    EVENT = "EVENT"
    MARKET = "MARKET"
    OUTCOME = "OUTCOME"
    CONSTRAINT = "CONSTRAINT"


class EdgeType(str, Enum):
    PART_OF = "PART_OF"
    HAS_MARKET = "HAS_MARKET"
    HAS_OUTCOME = "HAS_OUTCOME"
    PARTICIPATES_IN = "PARTICIPATES_IN"
    PRICES = "PRICES"
    QUALIFIES_FOR = "QUALIFIES_FOR"
    ADVANCES_TO = "ADVANCES_TO"
    IMPLIES = "IMPLIES"
    REFERS_TO = "REFERS_TO"
    EQUIVALENT = "EQUIVALENT"
    COMPLEMENT = "COMPLEMENT"
    MUTEX = "MUTEX"
    EXACTLY_ONE = "EXACTLY_ONE"


ALLOWED_EDGE_PATTERNS: dict[EdgeType, set[tuple[NodeType, NodeType]]] = {
    EdgeType.PART_OF: {
        (NodeType.STAGE, NodeType.COMPETITION),
        (NodeType.GROUP, NodeType.COMPETITION),
        (NodeType.ROUND, NodeType.COMPETITION),
        (NodeType.MATCH, NodeType.STAGE),
        (NodeType.MATCH, NodeType.GROUP),
        (NodeType.MATCH, NodeType.ROUND),
        (NodeType.GROUP, NodeType.STAGE),
        (NodeType.ROUND, NodeType.STAGE),
    },
    EdgeType.HAS_MARKET: {
        (NodeType.EVENT, NodeType.MARKET),
    },
    EdgeType.HAS_OUTCOME: {
        (NodeType.MARKET, NodeType.OUTCOME),
    },
    EdgeType.PARTICIPATES_IN: {
        (NodeType.TEAM, NodeType.COMPETITION),
        (NodeType.TEAM, NodeType.GROUP),
        (NodeType.TEAM, NodeType.MATCH),
        (NodeType.TEAM, NodeType.STAGE),
        (NodeType.TEAM, NodeType.ROUND),
    },
    EdgeType.PRICES: {
        (NodeType.MARKET, NodeType.TEAM),
        (NodeType.MARKET, NodeType.MATCH),
        (NodeType.MARKET, NodeType.OUTCOME),
    },
    EdgeType.QUALIFIES_FOR: {
        (NodeType.TEAM, NodeType.STAGE),
        (NodeType.TEAM, NodeType.ROUND),
        (NodeType.TEAM, NodeType.GROUP),
        (NodeType.MATCH, NodeType.ROUND),
    },
    EdgeType.ADVANCES_TO: {
        (NodeType.MATCH, NodeType.MATCH),
        (NodeType.TEAM, NodeType.ROUND),
        (NodeType.TEAM, NodeType.STAGE),
        (NodeType.STAGE, NodeType.STAGE),
    },
    EdgeType.IMPLIES: {
        (NodeType.MARKET, NodeType.MARKET),
        (NodeType.OUTCOME, NodeType.OUTCOME),
        (NodeType.MARKET, NodeType.OUTCOME),
    },
    EdgeType.REFERS_TO: {
        (NodeType.OUTCOME, NodeType.TEAM),
        (NodeType.OUTCOME, NodeType.MATCH),
        (NodeType.OUTCOME, NodeType.STAGE),
        (NodeType.OUTCOME, NodeType.COMPETITION),
        (NodeType.OUTCOME, NodeType.GROUP),
    },
    EdgeType.EQUIVALENT: {
        (NodeType.OUTCOME, NodeType.OUTCOME),
    },
    EdgeType.COMPLEMENT: {
        (NodeType.OUTCOME, NodeType.OUTCOME),
    },
    EdgeType.MUTEX: {
        (NodeType.OUTCOME, NodeType.OUTCOME),
    },
    EdgeType.EXACTLY_ONE: {
        (NodeType.CONSTRAINT, NodeType.OUTCOME),
    },
}

PROGRESSION_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.ADVANCES_TO, EdgeType.QUALIFIES_FOR}
)

LOGICAL_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {
        EdgeType.IMPLIES,
        EdgeType.EQUIVALENT,
        EdgeType.COMPLEMENT,
        EdgeType.MUTEX,
        EdgeType.EXACTLY_ONE,
    }
)


def is_allowed_edge(
    edge_type: EdgeType,
    source_type: NodeType,
    target_type: NodeType,
) -> bool:
    patterns = ALLOWED_EDGE_PATTERNS.get(edge_type, set())
    return (source_type, target_type) in patterns


def dump_ontology_json() -> dict[str, Any]:
    return {
        "node_types": [t.value for t in NodeType],
        "edge_types": [t.value for t in EdgeType],
        "allowed_edge_patterns": {
            et.value: [
                {"source": src.value, "target": tgt.value}
                for src, tgt in sorted(patterns, key=lambda p: (p[0].value, p[1].value))
            ]
            for et, patterns in ALLOWED_EDGE_PATTERNS.items()
        },
        "progression_edge_types": [t.value for t in PROGRESSION_EDGE_TYPES],
        "logical_edge_types": [t.value for t in LOGICAL_EDGE_TYPES],
    }
