from oddsgraph.config import Settings
from oddsgraph.ontology import NodeType
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import GraphFragment, Node

from tests.helpers import load_fixture_fragment


def test_exact_id_resolution() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert "team:turkiye" in state.canonical_nodes
    assert state.tier_counts.get("new_entity", 0) >= 1


def test_minimum_confidence_does_not_block_node_creation() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:xyz",
                type=NodeType.TEAM,
                label="Unique Team XYZ",
                confidence=0.4,
                evidence_market_ids=["1"],
            )
        ]
    )
    settings = Settings()
    settings.minimum_confidence = 0.5
    settings.fuzzy_threshold = 99
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert "team:unique-team-xyz" in state.canonical_nodes


def test_deterministic_and_llm_fragments_merge_same_canonical_id() -> None:
    det_fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:shared",
                type=NodeType.TEAM,
                label="Shared Team",
                confidence=0.9,
                evidence_market_ids=["m1"],
            )
        ]
    )
    llm_fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:llm-shared",
                type=NodeType.TEAM,
                label="Shared Team",
                confidence=0.8,
                evidence_market_ids=["m2"],
                aliases=["Shared"],
            )
        ]
    )
    settings = Settings()
    state = resolve_fragments(
        [det_fragment, llm_fragment],
        settings,
        inference_methods=["deterministic", "llm"],
    )
    canonical = state.canonical_nodes["team:shared-team"]
    assert canonical.confidence == 0.9
    assert sorted(canonical.evidence_market_ids) == ["m1", "m2"]
    assert "Shared" in canonical.aliases
