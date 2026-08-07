"""Tests for on-demand transitive IMPLIES closure."""

from __future__ import annotations

from oddsgraph.closure import compute_implies_closure
from oddsgraph.ontology import EdgeType
from oddsgraph.schema import CanonicalEdge


def _implies(src: str, tgt: str, evidence: str = "1") -> CanonicalEdge:
    return CanonicalEdge(
        source_id=src,
        target_id=tgt,
        edge_type=EdgeType.IMPLIES,
        confidence=1.0,
        evidence_market_ids=[evidence],
        derivation_type="rule",
        rule_id="test",
        rule_version=1,
        premises=[f"{src}->{tgt}"],
    )


def test_transitive_closure_emits_paths_and_skips_direct() -> None:
    edges = [
        _implies("A", "B", "1"),
        _implies("B", "C", "2"),
        _implies("C", "D", "3"),
        CanonicalEdge(
            source_id="X",
            target_id="Y",
            edge_type=EdgeType.PART_OF,
            confidence=1.0,
            evidence_market_ids=["9"],
        ),
    ]
    closure = compute_implies_closure(edges)
    pairs = {(e.source_id, e.target_id): e for e in closure}
    assert ("A", "B") not in pairs
    assert ("A", "C") in pairs
    assert pairs[("A", "C")].premises == ["A", "B", "C"]
    assert pairs[("A", "C")].derivation_type == "transitive"
    assert pairs[("A", "C")].confidence == 1.0
    assert ("A", "D") in pairs
    assert pairs[("A", "D")].premises == ["A", "B", "C", "D"]
    assert ("B", "D") in pairs


def test_empty_implies_returns_empty() -> None:
    assert compute_implies_closure([]) == []
    non_implies = [
        CanonicalEdge(
            source_id="A",
            target_id="B",
            edge_type=EdgeType.PART_OF,
            confidence=1.0,
            evidence_market_ids=["1"],
        )
    ]
    assert compute_implies_closure(non_implies) == []
