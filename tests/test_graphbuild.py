from oddsfox_graph.config import Settings
from oddsfox_graph.graphbuild import build_graph_from_fragments, validate_exported_graph
from oddsfox_graph.ontology import EdgeType, NodeType
from oddsfox_graph.resolution import resolve_fragments
from oddsfox_graph.schema import CanonicalEdge, CanonicalNode, Edge, GraphFragment, Node

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


def test_cycle_detection_on_advances_to() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:a",
                type=NodeType.TEAM,
                label="Team A",
                confidence=0.9,
                evidence_market_ids=["1"],
            ),
            Node(
                local_id="team:b",
                type=NodeType.TEAM,
                label="Team B",
                confidence=0.9,
                evidence_market_ids=["2"],
            ),
            Node(
                local_id="round:r16",
                type=NodeType.ROUND,
                label="Round of 16",
                confidence=0.9,
                evidence_market_ids=["1"],
            ),
        ],
        edges=[
            Edge(
                source="team:a",
                target="round:r16",
                type=EdgeType.ADVANCES_TO,
                confidence=0.8,
                evidence_market_ids=["1"],
                evidence_text="a advances",
            ),
            Edge(
                source="team:b",
                target="round:r16",
                type=EdgeType.QUALIFIES_FOR,
                confidence=0.8,
                evidence_market_ids=["2"],
                evidence_text="b qualifies",
            ),
            Edge(
                source="team:b",
                target="team:a",
                type=EdgeType.ADVANCES_TO,
                confidence=0.8,
                evidence_market_ids=["2"],
                evidence_text="cycle",
            ),
        ],
    )
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    assert any(
        e.rejection_reason == "progression_cycle" for e in result.rejected_edges
    ) or len(result.edges) < 3


def test_fixture_fragment_builds_valid_edges() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    errors = validate_exported_graph(result.nodes, result.edges)
    assert errors == []
    assert len(result.edges) >= 2
