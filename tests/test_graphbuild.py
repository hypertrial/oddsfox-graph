from oddsgraph.config import Settings
from oddsgraph.graphbuild import (
    _has_progression_cycle,
    build_graph_from_fragments,
    validate_exported_graph,
)
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import CanonicalEdge, Edge, GraphFragment, Node

from tests.helpers import load_fixture_fragment


def test_rejects_missing_endpoint() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:argentina",
                type=NodeType.TEAM,
                label="Argentina",
                confidence=0.9,
                evidence_market_ids=["1"],
            ),
        ],
        edges=[
            Edge(
                source="team:argentina",
                target="match:missing",
                type=EdgeType.PARTICIPATES_IN,
                confidence=0.9,
                evidence_market_ids=["1"],
                evidence_text="dangling",
            )
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    assert not result.edges
    assert any(e.rejection_reason == "missing_endpoint" for e in result.rejected_edges)


def test_rejects_invalid_edge_pattern() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:argentina",
                type=NodeType.TEAM,
                label="Argentina",
                confidence=0.9,
                evidence_market_ids=["1"],
            ),
            Node(
                local_id="market:999",
                type=NodeType.MARKET,
                label="Some market",
                confidence=1.0,
                evidence_market_ids=["999"],
            ),
        ],
        edges=[
            Edge(
                source="team:argentina",
                target="market:999",
                type=EdgeType.HAS_MARKET,
                confidence=0.8,
                evidence_market_ids=["1"],
                evidence_text="bad",
            )
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    assert any(e.rejection_reason == "invalid_pattern" for e in result.rejected_edges)


def test_has_progression_cycle_detects_match_cycle() -> None:
    edges = [
        CanonicalEdge(
            source_id="match:a",
            target_id="match:b",
            edge_type=EdgeType.ADVANCES_TO,
            confidence=0.9,
            evidence_market_ids=["1"],
            evidence_text="a to b",
        ),
        CanonicalEdge(
            source_id="match:b",
            target_id="match:a",
            edge_type=EdgeType.ADVANCES_TO,
            confidence=0.9,
            evidence_market_ids=["2"],
            evidence_text="b to a",
        ),
    ]
    assert _has_progression_cycle(edges)


def test_progression_cycle_rejects_only_cycle_edges() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="match:a",
                type=NodeType.MATCH,
                label="Match A",
                confidence=0.9,
                evidence_market_ids=["1"],
            ),
            Node(
                local_id="match:b",
                type=NodeType.MATCH,
                label="Match B",
                confidence=0.9,
                evidence_market_ids=["2"],
            ),
            Node(
                local_id="match:c",
                type=NodeType.MATCH,
                label="Match C",
                confidence=0.9,
                evidence_market_ids=["3"],
            ),
            Node(
                local_id="match:d",
                type=NodeType.MATCH,
                label="Match D",
                confidence=0.9,
                evidence_market_ids=["4"],
            ),
        ],
        edges=[
            Edge(
                source="match:a",
                target="match:b",
                type=EdgeType.ADVANCES_TO,
                confidence=0.9,
                evidence_market_ids=["1"],
                evidence_text="a to b",
            ),
            Edge(
                source="match:b",
                target="match:a",
                type=EdgeType.ADVANCES_TO,
                confidence=0.9,
                evidence_market_ids=["2"],
                evidence_text="b to a",
            ),
            Edge(
                source="match:c",
                target="match:d",
                type=EdgeType.ADVANCES_TO,
                confidence=0.9,
                evidence_market_ids=["3"],
                evidence_text="c to d",
            ),
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings, ["llm"])

    assert any(e.rejection_reason == "progression_cycle" for e in result.rejected_edges)
    kept = [
        (e.source_id, e.target_id)
        for e in result.edges
        if e.edge_type == EdgeType.ADVANCES_TO
    ]
    assert ("match:c", "match:d") in kept
    assert ("match:a", "match:b") not in kept or ("match:b", "match:a") not in kept
    assert validate_exported_graph(result.nodes, result.edges) == []


def test_fixture_fragment_builds_valid_edges() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    errors = validate_exported_graph(result.nodes, result.edges)
    assert errors == []
    assert len(result.edges) >= 2


def test_implies_cycle_is_rejected() -> None:
    from oddsgraph.graphbuild import _has_implies_cycle, reject_implies_cycle

    edges = [
        CanonicalEdge(
            source_id="outcome:a",
            target_id="outcome:b",
            edge_type=EdgeType.IMPLIES,
            confidence=1.0,
            evidence_market_ids=["1"],
        ),
        CanonicalEdge(
            source_id="outcome:b",
            target_id="outcome:a",
            edge_type=EdgeType.IMPLIES,
            confidence=1.0,
            evidence_market_ids=["2"],
        ),
    ]
    assert _has_implies_cycle(edges)
    kept, rejected = reject_implies_cycle(edges)
    assert kept == []
    assert len(rejected) == 2
    assert all(e.rejection_reason == "implies_cycle" for e in rejected)

    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="outcome:a",
                type=NodeType.OUTCOME,
                label="A",
                confidence=1.0,
                evidence_market_ids=["1"],
            ),
            Node(
                local_id="outcome:b",
                type=NodeType.OUTCOME,
                label="B",
                confidence=1.0,
                evidence_market_ids=["2"],
            ),
        ],
        edges=[
            Edge(
                source="outcome:a",
                target="outcome:b",
                type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["1"],
            ),
            Edge(
                source="outcome:b",
                target="outcome:a",
                type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["2"],
            ),
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="rule_engine")
    result = build_graph_from_fragments(
        [fragment], state, settings, fragment_methods=["rule_engine"]
    )
    assert any(e.rejection_reason == "implies_cycle" for e in result.rejected_edges)
    assert not any(e.edge_type == EdgeType.IMPLIES for e in result.edges)
    assert "implies cycle detected" in validate_exported_graph(
        result.nodes,
        [
            CanonicalEdge(
                source_id="outcome:a",
                target_id="outcome:b",
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["1"],
            ),
            CanonicalEdge(
                source_id="outcome:b",
                target_id="outcome:a",
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["2"],
            ),
        ],
    )
