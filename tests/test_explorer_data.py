"""Tests for oddsgraph.explorer.data DuckDB helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import rustworkx as rx

from oddsgraph.config import Settings
from oddsgraph.explorer import TOPOLOGY_NODE_TYPES
from oddsgraph.explorer.data import (
    bracket_elements,
    clear_stores,
    get_edge,
    get_node,
    get_store,
    graph_counts,
    node_neighbors,
    search_nodes,
    topology_elements,
)
from oddsgraph.export import export_graph_artifacts
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import CanonicalEdge, CanonicalNode, InferenceReport

from tests.helpers import make_settings


def _node(
    canonical_id: str,
    node_type: NodeType,
    label: str,
    *,
    aliases: list[str] | None = None,
    confidence: float = 1.0,
    evidence: list[str] | None = None,
    resolution_method: str = "exact_id",
    inference_method: str = "deterministic",
) -> CanonicalNode:
    return CanonicalNode(
        canonical_id=canonical_id,
        type=node_type,
        label=label,
        aliases=aliases or [],
        confidence=confidence,
        evidence_market_ids=evidence or ["m1"],
        resolution_method=resolution_method,
        inference_method=inference_method,
    )


def _edge(
    source_id: str,
    target_id: str,
    edge_type: EdgeType,
    *,
    confidence: float = 1.0,
    evidence_text: str = "",
    inference_method: str = "deterministic",
) -> CanonicalEdge:
    return CanonicalEdge(
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        confidence=confidence,
        evidence_market_ids=["m1"],
        evidence_text=evidence_text,
        inference_method=inference_method,
    )


def _write_fixture_graph(build_dir: Path) -> None:
    nodes = [
        _node(
            "competition:world-cup-2026",
            NodeType.COMPETITION,
            "World Cup 2026",
            aliases=["WC2026"],
            resolution_method="seed",
            inference_method="official_bracket",
        ),
        _node("stage:world-cup-2026:group-stage", NodeType.STAGE, "Group Stage"),
        _node("stage:world-cup-2026:round-of-16", NodeType.STAGE, "Round of 16"),
        _node("stage:world-cup-2026:final", NodeType.STAGE, "Final"),
        _node(
            "match:brazil-vs-france",
            NodeType.MATCH,
            "Brazil vs. France",
            aliases=["bra-fra"],
            confidence=0.95,
        ),
        _node(
            "match:group-a-opener",
            NodeType.MATCH,
            "Mexico vs. South Africa",
            aliases=["mex-rsa"],
        ),
        _node(
            "match:final",
            NodeType.MATCH,
            "Brazil vs. Spain",
            aliases=["bra-esp-final"],
        ),
        _node(
            "team:brazil",
            NodeType.TEAM,
            "Brazil",
            aliases=["bra", "Seleção"],
            confidence=0.99,
        ),
        _node(
            "team:france",
            NodeType.TEAM,
            "France",
            aliases=["fra"],
            confidence=0.99,
        ),
        _node("event:100", NodeType.EVENT, "Brazil vs. France Markets"),
        _node("market:m1", NodeType.MARKET, "Match Winner"),
        _node("outcome:m1:brazil", NodeType.OUTCOME, "Brazil"),
    ]
    edges = [
        _edge(
            "match:brazil-vs-france",
            "stage:world-cup-2026:round-of-16",
            EdgeType.PART_OF,
            confidence=0.9,
            evidence_text="Round of 16",
            inference_method="official_bracket",
        ),
        _edge(
            "match:group-a-opener",
            "stage:world-cup-2026:group-stage",
            EdgeType.PART_OF,
            evidence_text="Group Stage",
            inference_method="official_bracket",
        ),
        _edge(
            "match:final",
            "stage:world-cup-2026:final",
            EdgeType.PART_OF,
            evidence_text="Final",
            inference_method="official_bracket",
        ),
        _edge(
            "match:brazil-vs-france",
            "match:final",
            EdgeType.ADVANCES_TO,
            evidence_text="team continuity across consecutive knockout stages",
            inference_method="official_bracket",
        ),
        _edge(
            "team:brazil",
            "match:brazil-vs-france",
            EdgeType.PARTICIPATES_IN,
            confidence=0.95,
            evidence_text="Brazil plays",
        ),
        _edge(
            "team:france",
            "match:brazil-vs-france",
            EdgeType.PARTICIPATES_IN,
            confidence=0.95,
            evidence_text="France plays",
        ),
        _edge("event:100", "market:m1", EdgeType.HAS_MARKET),
        _edge("market:m1", "outcome:m1:brazil", EdgeType.HAS_OUTCOME),
    ]
    report = InferenceReport(
        events_processed=1,
        node_counts={
            "COMPETITION": 1,
            "STAGE": 3,
            "MATCH": 3,
            "TEAM": 2,
            "EVENT": 1,
            "MARKET": 1,
            "OUTCOME": 1,
        },
        edge_counts={
            "PART_OF": 3,
            "ADVANCES_TO": 1,
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


@pytest.fixture(autouse=True)
def _clear_explorer_stores() -> None:
    clear_stores()
    yield
    clear_stores()


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


def test_canvas_elements_strip_evidence_payloads(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    topo = topology_elements(settings)
    bracket = bracket_elements(settings)
    for el in [*topo.nodes, *topo.edges, *bracket.nodes, *bracket.edges]:
        data = el["data"]
        assert "evidence_market_ids" not in data
        assert "evidence_text" not in data
        assert data["evidence_count"] >= 1

    row = get_node(settings, "team:brazil")
    assert row is not None
    assert row["evidence_market_ids"] == ["m1"]
    edge = get_edge(
        settings,
        "team:brazil",
        "match:brazil-vs-france",
        "PARTICIPATES_IN",
    )
    assert edge is not None
    assert edge["evidence_market_ids"] == ["m1"]
    assert edge["evidence_text"] == "Brazil plays"


def test_get_store_caches_topology_and_refreshes_on_mtime(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    store = get_store(settings)
    first = store.topology_elements()
    second = store.topology_elements()
    assert first is second

    # Touch parquet so the store closes and rebuilds caches.
    settings.nodes_path.write_bytes(settings.nodes_path.read_bytes())
    store.refresh_if_stale()
    third = store.topology_elements()
    assert third is not first
    assert len(third.nodes) == len(first.nodes)


def test_node_neighbors_sets_truncated_when_limit_hit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    full = node_neighbors(settings, "match:brazil-vs-france", limit=300)
    assert full.truncated is False
    assert len(full.edges) >= 2

    limited = node_neighbors(settings, "match:brazil-vs-france", limit=1)
    assert limited.truncated is True
    assert len(limited.edges) == 1


def test_bracket_elements_returns_only_knockout_matches(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = bracket_elements(settings)
    node_ids = {el["data"]["id"] for el in slice_.nodes}
    node_types = {el["data"]["type"] for el in slice_.nodes}
    edge_types = {el["data"]["edge_type"] for el in slice_.edges}

    assert node_types == {"MATCH"}
    assert node_ids == {"match:brazil-vs-france", "match:final"}
    assert "match:group-a-opener" not in node_ids
    assert edge_types == {"ADVANCES_TO"}
    assert len(slice_.edges) == 1
    assert slice_.edges[0]["data"]["source"] == "match:brazil-vs-france"
    assert slice_.edges[0]["data"]["target"] == "match:final"


def test_bracket_elements_enrich_stage_labels_and_positions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = bracket_elements(settings)
    by_id = {el["data"]["id"]: el for el in slice_.nodes}

    r16 = by_id["match:brazil-vs-france"]
    final = by_id["match:final"]
    assert r16["data"]["stage"] == "Round of 16"
    assert final["data"]["stage"] == "Final"
    assert r16["data"]["stage_rank"] == 2
    assert final["data"]["stage_rank"] == 5
    assert r16["data"]["short_label"] == "Brazil\nFrance"
    assert final["data"]["short_label"] == "Brazil\nSpain"
    assert "position" in r16 and "position" in final
    assert r16["position"]["x"] < final["position"]["x"]
    # Final sits near the vertical midpoint of its predecessor.
    assert abs(final["position"]["y"] - r16["position"]["y"]) < 1e-6


def test_bracket_elements_is_acyclic(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = bracket_elements(settings)
    graph = rx.PyDiGraph()
    index: dict[str, int] = {}
    for node in slice_.nodes:
        node_id = node["data"]["id"]
        index[node_id] = graph.add_node(node_id)
    for edge in slice_.edges:
        graph.add_edge(
            index[edge["data"]["source"]],
            index[edge["data"]["target"]],
            edge["data"]["edge_type"],
        )
    assert rx.is_directed_acyclic_graph(graph)


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
    assert "stage:world-cup-2026:round-of-16" in match_ids
    assert "match:final" in match_ids
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


def test_graph_counts_from_inference_report(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    counts = graph_counts(settings)
    assert counts["source"] == "inference_report"
    assert counts["total_nodes"] == 12
    assert counts["total_edges"] == 8
    assert counts["node_counts"]["TEAM"] == 2
    assert counts["edge_counts"]["HAS_MARKET"] == 1


def test_bracket_elements_on_real_build_if_present() -> None:
    """Optional smoke against a local full build (skipped when artifacts missing)."""
    settings = Settings()
    if not settings.nodes_path.exists() or not settings.edges_path.exists():
        pytest.skip("build/nodes.parquet and build/edges.parquet not present")
    slice_ = bracket_elements(settings)
    assert len(slice_.nodes) == 32
    assert len(slice_.edges) == 32
    assert {n["data"]["type"] for n in slice_.nodes} == {"MATCH"}
    assert {e["data"]["edge_type"] for e in slice_.edges} == {"ADVANCES_TO"}
    assert all(n["data"].get("stage") for n in slice_.nodes)
    assert all(n["data"].get("short_label") for n in slice_.nodes)
    assert all("position" in n for n in slice_.nodes)

    xs = {n["data"]["stage"]: n["position"]["x"] for n in slice_.nodes}
    assert xs["Round of 32"] < xs["Round of 16"] < xs["Quarterfinals"]
    assert xs["Semifinals"] < xs["Final"]
    assert xs["Final"] == xs["Third Place"]

    finals = [n for n in slice_.nodes if n["data"]["stage"] == "Final"]
    thirds = [n for n in slice_.nodes if n["data"]["stage"] == "Third Place"]
    assert len(finals) == 1 and len(thirds) == 1
    assert thirds[0]["position"]["y"] > finals[0]["position"]["y"]

    graph = rx.PyDiGraph()
    index: dict[str, int] = {}
    for node in slice_.nodes:
        node_id = node["data"]["id"]
        index[node_id] = graph.add_node(node_id)
    for edge in slice_.edges:
        graph.add_edge(
            index[edge["data"]["source"]],
            index[edge["data"]["target"]],
            edge["data"]["edge_type"],
        )
    assert rx.is_directed_acyclic_graph(graph)
