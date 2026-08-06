import pytest
from pydantic import ValidationError

from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node, merge_fragments


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


def test_merge_fragments_uses_max_confidence_for_duplicate_edges() -> None:
    fragment_low = GraphFragment(
        nodes=[
            Node(
                local_id="team:a",
                type=NodeType.TEAM,
                label="A",
                confidence=0.5,
                evidence_market_ids=["1"],
            ),
            Node(
                local_id="match:m",
                type=NodeType.MATCH,
                label="M",
                confidence=0.5,
                evidence_market_ids=["1"],
            ),
        ],
        edges=[
            Edge(
                source="team:a",
                target="match:m",
                type=EdgeType.PARTICIPATES_IN,
                confidence=0.5,
                evidence_market_ids=["1"],
                evidence_text="low",
            )
        ],
    )
    fragment_high = GraphFragment(
        nodes=[
            Node(
                local_id="team:a",
                type=NodeType.TEAM,
                label="A",
                confidence=0.9,
                evidence_market_ids=["2"],
            )
        ],
        edges=[
            Edge(
                source="team:a",
                target="match:m",
                type=EdgeType.PARTICIPATES_IN,
                confidence=0.9,
                evidence_market_ids=["2"],
                evidence_text="high",
            )
        ],
    )
    merged = merge_fragments([fragment_low, fragment_high])
    assert len(merged.edges) == 1
    assert merged.edges[0].confidence == 0.9
    assert sorted(merged.edges[0].evidence_market_ids) == ["1", "2"]
    assert merged.edges[0].evidence_text == "high"
    team = next(n for n in merged.nodes if n.local_id == "team:a")
    assert team.confidence == 0.9
    assert sorted(team.evidence_market_ids) == ["1", "2"]

    # Higher confidence first, then lower: still keep the stronger evidence_text.
    merged_rev = merge_fragments([fragment_high, fragment_low])
    assert merged_rev.edges[0].confidence == 0.9
    assert merged_rev.edges[0].evidence_text == "high"
