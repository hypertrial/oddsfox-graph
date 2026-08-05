from oddsfox_graph.config import Settings
from oddsfox_graph.ontology import NodeType
from oddsfox_graph.resolution import resolve_fragments
from oddsfox_graph.schema import GraphFragment, Node

from tests.helpers import load_fixture_fragment


def test_exact_id_resolution() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert "team:turkiye" in state.canonical_nodes
    assert state.tier_counts.get("new_entity", 0) >= 1


def test_fuzzy_below_threshold_creates_new_entity() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:totally-different",
                type=NodeType.TEAM,
                label="Xyz Unknown Nation",
                confidence=0.5,
                evidence_market_ids=["999"],
            )
        ]
    )
    settings = Settings()
    settings.fuzzy_threshold = 99
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert len(state.unresolved) == 0
    assert "team:xyz-unknown-nation" in state.canonical_nodes
