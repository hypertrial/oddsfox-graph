import pytest
from pydantic import ValidationError

from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node


def test_graph_fragment_validates() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:argentina",
                type=NodeType.TEAM,
                label="Argentina",
                confidence=0.9,
                evidence_market_ids=["123"],
            )
        ],
        edges=[
            Edge(
                source="team:argentina",
                target="competition:world-cup-2026",
                type=EdgeType.PARTICIPATES_IN,
                confidence=0.8,
                evidence_market_ids=["123"],
                evidence_text="evidence",
            )
        ],
    )
    assert len(fragment.nodes) == 1
    assert len(fragment.edges) == 1


def test_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        Node(
            local_id="team:argentina",
            type=NodeType.TEAM,
            label="Argentina",
            confidence=1.5,
            evidence_market_ids=["123"],
        )


def test_rejects_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        Edge(
            source="a",
            target="b",
            type=EdgeType.PART_OF,
            confidence=0.5,
            evidence_market_ids=[],
            evidence_text="",
        )
