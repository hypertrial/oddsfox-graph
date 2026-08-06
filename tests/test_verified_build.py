"""Tests for verified topology superseding template topology on build."""

from __future__ import annotations

from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.ontology import NodeType
from oddsgraph.pipeline import build_pipeline_from_markets
from oddsgraph.schema import GraphFragment, Node, SemanticMarket


def _match_market(event_id: str = "match-evt") -> SemanticMarket:
    return SemanticMarket(
        market_id="m1",
        event_id=event_id,
        event_title="Brazil vs. Morocco - Exact Score",
        event_slug="fifwc-bra-mar-2026-06-14",
        question="Winner?",
        outcomes=["Brazil", "Morocco"],
        sports_market_type="soccer_match",
    )


def test_verified_topology_replaces_template_edges(tmp_path: Path) -> None:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    settings.deterministic_topology = True
    settings.official_bracket = False

    markets = [_match_market()]
    with_template = build_deterministic_fragments_by_event(markets)
    assert any(n.type == NodeType.MATCH for n in with_template["match-evt"].nodes)

    # Verified fragment supplies topology replacement (TEAM only, no MATCH).
    verified = GraphFragment(
        nodes=[
            Node(
                local_id="team:brazil",
                type=NodeType.TEAM,
                label="Brazil",
                confidence=1.0,
                evidence_market_ids=["m1"],
            )
        ],
        edges=[],
    )
    result = build_pipeline_from_markets(
        settings,
        markets,
        inferred_fragments={"match-evt": verified},
        verified_event_ids={"match-evt"},
    )
    match_nodes = [n for n in result.graph.nodes if n.type == NodeType.MATCH]
    assert match_nodes == []
    assert any(n.type == NodeType.TEAM and n.label == "Brazil" for n in result.graph.nodes)
