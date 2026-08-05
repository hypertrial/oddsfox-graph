from oddsfox_graph.config import Settings
from oddsfox_graph.graphbuild import (
    _has_progression_cycle,
    build_graph_from_fragments,
    validate_exported_graph,
)
from oddsfox_graph.ontology import EdgeType, NodeType
from oddsfox_graph.resolution import resolve_fragments
from oddsfox_graph.schema import CanonicalEdge, Edge, GraphFragment, Node

from tests.helpers import load_fixture_fragment


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


def test_progression_cycle_rejects_advances_to_edges() -> None:
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
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings, ["llm"])

    assert not any(e.edge_type == EdgeType.ADVANCES_TO for e in result.edges)
    assert any(e.rejection_reason == "progression_cycle" for e in result.rejected_edges)
    assert validate_exported_graph(result.nodes, result.edges) == []


def test_fixture_fragment_builds_valid_edges() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    errors = validate_exported_graph(result.nodes, result.edges)
    assert errors == []
    assert len(result.edges) >= 2
