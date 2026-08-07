from oddsgraph.ontology import (
    ALLOWED_EDGE_PATTERNS,
    LOGICAL_EDGE_TYPES,
    EdgeType,
    NodeType,
    is_allowed_edge,
)
from oddsgraph.prompts import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES


def test_allowed_edge_patterns_include_spec_examples() -> None:
    assert is_allowed_edge(EdgeType.PARTICIPATES_IN, NodeType.TEAM, NodeType.COMPETITION)
    assert is_allowed_edge(EdgeType.HAS_MARKET, NodeType.EVENT, NodeType.MARKET)
    assert is_allowed_edge(EdgeType.HAS_OUTCOME, NodeType.MARKET, NodeType.OUTCOME)
    assert is_allowed_edge(EdgeType.ADVANCES_TO, NodeType.MATCH, NodeType.MATCH)
    assert is_allowed_edge(EdgeType.ADVANCES_TO, NodeType.STAGE, NodeType.STAGE)
    assert is_allowed_edge(EdgeType.PART_OF, NodeType.ROUND, NodeType.COMPETITION)
    assert is_allowed_edge(EdgeType.REFERS_TO, NodeType.OUTCOME, NodeType.TEAM)
    assert is_allowed_edge(EdgeType.EXACTLY_ONE, NodeType.CONSTRAINT, NodeType.OUTCOME)
    assert is_allowed_edge(EdgeType.COMPLEMENT, NodeType.OUTCOME, NodeType.OUTCOME)
    assert is_allowed_edge(EdgeType.MUTEX, NodeType.OUTCOME, NodeType.OUTCOME)
    assert is_allowed_edge(EdgeType.EQUIVALENT, NodeType.OUTCOME, NodeType.OUTCOME)


def test_rejects_invalid_pattern() -> None:
    assert not is_allowed_edge(EdgeType.HAS_MARKET, NodeType.TEAM, NodeType.MARKET)
    assert not is_allowed_edge(EdgeType.REFERS_TO, NodeType.MARKET, NodeType.TEAM)


def test_all_edge_types_have_patterns() -> None:
    for edge_type in EdgeType:
        assert edge_type in ALLOWED_EDGE_PATTERNS


def test_logical_edge_types_constant() -> None:
    assert EdgeType.IMPLIES in LOGICAL_EDGE_TYPES
    assert EdgeType.EXACTLY_ONE in LOGICAL_EDGE_TYPES
    assert EdgeType.PART_OF not in LOGICAL_EDGE_TYPES
    assert NodeType.CONSTRAINT.value == "CONSTRAINT"


def test_llm_allowed_edges_are_realizable_with_allowed_nodes() -> None:
    for edge_type in ALLOWED_EDGE_TYPES:
        patterns = ALLOWED_EDGE_PATTERNS[edge_type]
        realizable = [
            (src, tgt)
            for src, tgt in patterns
            if src in ALLOWED_NODE_TYPES and tgt in ALLOWED_NODE_TYPES
        ]
        assert realizable, f"{edge_type.value} has no patterns using ALLOWED_NODE_TYPES"


def test_llm_allowlist_excludes_market_only_edges() -> None:
    assert EdgeType.PRICES not in ALLOWED_EDGE_TYPES
    assert EdgeType.IMPLIES not in ALLOWED_EDGE_TYPES
    assert EdgeType.REFERS_TO not in ALLOWED_EDGE_TYPES
    assert EdgeType.COMPLEMENT not in ALLOWED_EDGE_TYPES
