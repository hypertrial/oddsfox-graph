"""Tests for oddsgraph.explorer.data DuckDB helpers."""

from __future__ import annotations

from pathlib import Path

from oddsgraph.explorer import TOPOLOGY_NODE_TYPES
from oddsgraph.explorer.data import (
    get_edge,
    get_node,
    graph_counts,
    node_neighbors,
    search_nodes,
    topology_elements,
)
from oddsgraph.export import export_graph_artifacts
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport

from tests.helpers import make_settings


def _write_fixture_graph(build_dir: Path) -> None:
    nodes = [
        CanonicalNode(
            canonical_id="competition:world-cup-2026",
            type=NodeType.COMPETITION,
            label="World Cup 2026",
            aliases=["WC2026"],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="seed",
            inference_method="official_bracket",
        ),
        CanonicalNode(
            canonical_id="match:brazil-vs-france",
            type=NodeType.MATCH,
            label="Brazil vs. France",
            aliases=["bra-fra"],
            confidence=0.95,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
        CanonicalNode(
            canonical_id="team:brazil",
            type=NodeType.TEAM,
            label="Brazil",
            aliases=["bra", "Seleção"],
            confidence=0.99,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
        CanonicalNode(
            canonical_id="team:france",
            type=NodeType.TEAM,
            label="France",
            aliases=["fra"],
            confidence=0.99,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
        CanonicalNode(
            canonical_id="event:100",
            type=NodeType.EVENT,
            label="Brazil vs. France Markets",
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
        CanonicalNode(
            canonical_id="market:m1",
            type=NodeType.MARKET,
            label="Match Winner",
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
        CanonicalNode(
            canonical_id="outcome:m1:brazil",
            type=NodeType.OUTCOME,
            label="Brazil",
            aliases=[],
            confidence=1.0,
            evidence_market_ids=["m1"],
            resolution_method="exact_id",
            inference_method="deterministic",
        ),
    ]
    edges = [
        CanonicalEdge(
            source_id="match:brazil-vs-france",
            target_id="competition:world-cup-2026",
            edge_type=EdgeType.PART_OF,
            confidence=0.9,
            evidence_market_ids=["m1"],
            evidence_text="match belongs to competition",
            inference_method="official_bracket",
        ),
        CanonicalEdge(
            source_id="team:brazil",
            target_id="match:brazil-vs-france",
            edge_type=EdgeType.PARTICIPATES_IN,
            confidence=0.95,
            evidence_market_ids=["m1"],
            evidence_text="Brazil plays",
            inference_method="deterministic",
        ),
        CanonicalEdge(
            source_id="team:france",
            target_id="match:brazil-vs-france",
            edge_type=EdgeType.PARTICIPATES_IN,
            confidence=0.95,
            evidence_market_ids=["m1"],
            evidence_text="France plays",
            inference_method="deterministic",
        ),
        CanonicalEdge(
            source_id="event:100",
            target_id="market:m1",
            edge_type=EdgeType.HAS_MARKET,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="",
            inference_method="deterministic",
        ),
        CanonicalEdge(
            source_id="market:m1",
            target_id="outcome:m1:brazil",
            edge_type=EdgeType.HAS_OUTCOME,
            confidence=1.0,
            evidence_market_ids=["m1"],
            evidence_text="",
            inference_method="deterministic",
        ),
    ]
    report = InferenceReport(
        events_processed=1,
        node_counts={
            "COMPETITION": 1,
            "MATCH": 1,
            "TEAM": 2,
            "EVENT": 1,
            "MARKET": 1,
            "OUTCOME": 1,
        },
        edge_counts={
            "PART_OF": 1,
            "PARTICIPATES_IN": 2,
            "HAS_MARKET": 1,
            "HAS_OUTCOME": 1,
        },
    )
    export_graph_artifacts(
        nodes=nodes,
        edges=edges,
        rejected_edges=[],
        report=report,
        nodes_path=build_dir / "nodes.parquet",
        edges_path=build_dir / "edges.parquet",
        rejected_edges_path=build_dir / "rejected_edges.parquet",
        ontology_path=build_dir / "ontology.json",
        inference_report_path=build_dir / "inference_report.json",
    )


def test_topology_elements_excludes_market_layer(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = topology_elements(settings)
    node_ids = {el["data"]["id"] for el in slice_.nodes}
    node_types = {el["data"]["type"] for el in slice_.nodes}
    edge_types = {el["data"]["edge_type"] for el in slice_.edges}

    assert node_types <= TOPOLOGY_NODE_TYPES
    assert "event:100" not in node_ids
    assert "market:m1" not in node_ids
    assert "outcome:m1:brazil" not in node_ids
    assert "match:brazil-vs-france" in node_ids
    assert "team:brazil" in node_ids
    assert "HAS_MARKET" not in edge_types
    assert "HAS_OUTCOME" not in edge_types
    assert "PARTICIPATES_IN" in edge_types
    assert "PART_OF" in edge_types


def test_search_nodes_matches_label_alias_and_limit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    by_label = search_nodes(settings, "brazil", limit=25)
    labels = {row["canonical_id"] for row in by_label}
    assert "team:brazil" in labels
    assert "match:brazil-vs-france" in labels

    by_alias = search_nodes(settings, "Seleção", limit=25)
    assert any(row["canonical_id"] == "team:brazil" for row in by_alias)

    limited = search_nodes(settings, "brazil", limit=1)
    assert len(limited) == 1

    empty = search_nodes(settings, "   ", limit=25)
    assert empty == []


def test_node_neighbors_match_and_event_are_disconnected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    match_slice = node_neighbors(settings, "match:brazil-vs-france")
    match_ids = {el["data"]["id"] for el in match_slice.nodes}
    assert "team:brazil" in match_ids
    assert "team:france" in match_ids
    assert "competition:world-cup-2026" in match_ids
    assert "event:100" not in match_ids
    assert "market:m1" not in match_ids

    event_slice = node_neighbors(settings, "event:100")
    event_ids = {el["data"]["id"] for el in event_slice.nodes}
    assert "market:m1" in event_ids
    assert "match:brazil-vs-france" not in event_ids
    assert "team:brazil" not in event_ids


def test_get_node_and_get_edge_missing_return_none(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    assert get_node(settings, "team:brazil") is not None
    assert get_node(settings, "team:missing") is None
    assert get_node(settings, "") is None

    edge = get_edge(
        settings,
        "team:brazil",
        "match:brazil-vs-france",
        "PARTICIPATES_IN",
    )
    assert edge is not None
    assert edge["evidence_text"] == "Brazil plays"

    assert (
        get_edge(settings, "team:brazil", "match:brazil-vs-france", "PRICES") is None
    )
    assert get_edge(settings, "", "x", "PART_OF") is None


def test_search_nodes_escapes_like_wildcards(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    # A literal '%' should not match every label via ILIKE wildcards.
    rows = search_nodes(settings, "%", limit=25)
    assert rows == []

    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    counts = graph_counts(settings)
    assert counts["source"] == "inference_report"
    assert counts["total_nodes"] == 7
    assert counts["total_edges"] == 5
    assert counts["node_counts"]["TEAM"] == 2
    assert counts["edge_counts"]["HAS_MARKET"] == 1
