"""End-to-end residual LLM fragment → resolve → build coverage."""

from __future__ import annotations

from pathlib import Path

from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.pipeline import build_pipeline_from_markets
from oddsgraph.schema import Edge, GraphFragment, Node, SemanticMarket
from tests.helpers import make_settings


def test_residual_llm_fragment_builds_with_deterministic_markets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.official_bracket = False

    markets = [
        SemanticMarket(
            market_id="gb1",
            event_id="golden-ball",
            event_title="World Cup Golden Ball Winner",
            question="Who wins the Golden Ball?",
            outcomes=["Yes", "No"],
            sports_market_type="other",
        )
    ]
    llm_fragment = GraphFragment(
        nodes=[
            Node(
                local_id="competition:world-cup-2026",
                type=NodeType.COMPETITION,
                label="World Cup 2026",
                confidence=0.9,
                evidence_market_ids=["gb1"],
            ),
            Node(
                local_id="team:brazil",
                type=NodeType.TEAM,
                label="Brazil",
                confidence=0.9,
                evidence_market_ids=["gb1"],
            ),
            Node(
                local_id="stage:champion",
                type=NodeType.STAGE,
                label="Champion",
                confidence=0.8,
                evidence_market_ids=["gb1"],
            ),
        ],
        edges=[
            Edge(
                source="team:brazil",
                target="stage:champion",
                type=EdgeType.QUALIFIES_FOR,
                confidence=0.8,
                evidence_market_ids=["gb1"],
                evidence_text="Golden Ball residual prop",
            )
        ],
    )

    result = build_pipeline_from_markets(
        settings,
        markets,
        inferred_fragments={"golden-ball": llm_fragment},
    )

    labels = {n.label for n in result.graph.nodes if n.type == NodeType.TEAM}
    assert "Brazil" in labels
    assert any(e.edge_type == EdgeType.QUALIFIES_FOR for e in result.graph.edges)
    assert any(
        e.inference_method == "llm" for e in result.graph.edges if e.edge_type == EdgeType.QUALIFIES_FOR
    )
