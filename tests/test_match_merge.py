"""Tests for MATCH identity merge policy."""

from __future__ import annotations

from oddsgraph.match_merge import (
    match_bind_allowed,
    match_id_upgrade_target,
    more_specific_match_id,
    near_date_same_fixture,
    preferred_inference_method,
)
from oddsgraph.ontology import NodeType
from oddsgraph.schema import CanonicalNode, Node


def test_near_date_and_specificity_helpers() -> None:
    assert more_specific_match_id(
        "match:spain-vs-france-2026-07-05",
        "match:spain-vs-france",
    )
    assert near_date_same_fixture(
        "match:spain-vs-france-2026-07-05",
        "match:spain-vs-france-2026-07-06",
    )
    assert not near_date_same_fixture(
        "match:spain-vs-france-2026-07-05",
        "match:spain-vs-france-2026-08-05",
    )
    assert preferred_inference_method("llm", "official_bracket") == "official_bracket"


def test_match_bind_and_upgrade_prefer_official_bracket_date() -> None:
    existing = CanonicalNode(
        canonical_id="match:spain-vs-france-2026-07-05",
        type=NodeType.MATCH,
        label="Spain vs. France",
        aliases=[],
        confidence=1.0,
        evidence_market_ids=[],
        resolution_method="exact_id",
        inference_method="llm",
    )
    node = Node(
        local_id="match:spain-vs-france-2026-07-06",
        type=NodeType.MATCH,
        label="Spain vs. France",
        aliases=[],
        confidence=1.0,
        evidence_market_ids=["m1"],
    )
    assert match_bind_allowed(existing, node)
    assert (
        match_id_upgrade_target(existing, node, "official_bracket")
        == "match:spain-vs-france-2026-07-06"
    )
