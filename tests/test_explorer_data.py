"""Tests for oddsgraph.explorer.data DuckDB helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import rustworkx as rx

from oddsgraph.config import Settings
from oddsgraph.explorer.data import (
    bracket_elements,
    clear_stores,
    get_edge,
    get_node,
    get_store,
    graph_counts,
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


def test_canvas_elements_strip_evidence_payloads(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    bracket = bracket_elements(settings)
    for el in [*bracket.nodes, *bracket.edges]:
        data = el["data"]
        assert "evidence_market_ids" not in data
        assert "evidence_text" not in data
        if data.get("type") == "STAGE_HEADER":
            assert data["evidence_count"] == 0
            continue
        assert data["evidence_count"] >= 1

    row = get_node(settings, "match:brazil-vs-france")
    assert row is not None
    assert row["evidence_market_ids"] == ["m1"]
    edge = get_edge(
        settings,
        "match:brazil-vs-france",
        "match:final",
        "ADVANCES_TO",
    )
    assert edge is not None
    assert edge["evidence_market_ids"] == ["m1"]


def test_get_store_caches_bracket_and_refreshes_on_mtime(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    store = get_store(settings)
    first = store.bracket_elements()
    second = store.bracket_elements()
    assert first is second

    # Touch parquet so the store closes and rebuilds caches.
    settings.nodes_path.write_bytes(settings.nodes_path.read_bytes())
    store.refresh_if_stale()
    third = store.bracket_elements()
    assert third is not first
    assert len(third.nodes) == len(first.nodes)


def test_bracket_elements_returns_only_knockout_matches(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = bracket_elements(settings)
    match_nodes = [el for el in slice_.nodes if el["data"]["type"] == "MATCH"]
    headers = [el for el in slice_.nodes if el["data"]["type"] == "STAGE_HEADER"]
    node_ids = {el["data"]["id"] for el in match_nodes}
    node_types = {el["data"]["type"] for el in match_nodes}
    edge_types = {el["data"]["edge_type"] for el in slice_.edges}

    assert node_types == {"MATCH"}
    assert node_ids == {"match:brazil-vs-france", "match:final"}
    assert "match:group-a-opener" not in node_ids
    assert edge_types == {"ADVANCES_TO"}
    assert len(slice_.edges) == 1
    assert slice_.edges[0]["data"]["source"] == "match:brazil-vs-france"
    assert slice_.edges[0]["data"]["target"] == "match:final"
    assert {h["data"]["label"] for h in headers} == {"Round of 16", "Final / 3rd"}
    assert all(h["selectable"] is False for h in headers)


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
    r16_header = by_id["stage-header:1"]
    final_header = by_id["stage-header:4"]
    assert r16_header["data"]["label"] == "Round of 16"
    assert final_header["data"]["label"] == "Final / 3rd"
    assert r16_header["position"]["y"] < r16["position"]["y"]
    assert r16_header["position"]["x"] == r16["position"]["x"]
    assert final_header["position"]["x"] == final["position"]["x"]


def test_bracket_elements_is_acyclic(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    slice_ = bracket_elements(settings)
    graph = rx.PyDiGraph()
    index: dict[str, int] = {}
    for node in slice_.nodes:
        if node["data"]["type"] != "MATCH":
            continue
        node_id = node["data"]["id"]
        index[node_id] = graph.add_node(node_id)
    for edge in slice_.edges:
        graph.add_edge(
            index[edge["data"]["source"]],
            index[edge["data"]["target"]],
            edge["data"]["edge_type"],
        )
    assert rx.is_directed_acyclic_graph(graph)


def test_get_node_and_get_edge_missing_return_none(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    assert get_node(settings, "match:brazil-vs-france") is not None
    assert get_node(settings, "team:missing") is None
    assert get_node(settings, "") is None

    edge = get_edge(
        settings,
        "match:brazil-vs-france",
        "match:final",
        "ADVANCES_TO",
    )
    assert edge is not None
    assert edge["evidence_text"] == "team continuity across consecutive knockout stages"

    assert (
        get_edge(settings, "match:brazil-vs-france", "match:final", "PRICES") is None
    )
    assert get_edge(settings, "", "x", "PART_OF") is None


def test_graph_counts_from_inference_report(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)

    counts = graph_counts(settings)
    assert counts["source"] == "inference_report"
    assert counts["total_nodes"] == 12
    assert counts["total_edges"] == 8
    assert counts["node_counts"]["TEAM"] == 2
    assert counts["edge_counts"]["HAS_MARKET"] == 1


def test_graph_counts_falls_back_when_report_histograms_empty(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    export_graph_artifacts(
        nodes=[
            _node("team:a", NodeType.TEAM, "A"),
        ],
        edges=[],
        rejected_edges=[],
        report=InferenceReport(),
        nodes_path=settings.nodes_path,
        edges_path=settings.edges_path,
        rejected_edges_path=settings.rejected_edges_path,
        ontology_path=settings.ontology_path,
        inference_report_path=settings.inference_report_path,
    )
    clear_stores()
    counts = graph_counts(settings)
    assert counts["source"] == "parquet"
    assert counts["total_nodes"] == 1
    assert counts["node_counts"]["TEAM"] == 1


def test_missing_parquet_stubs_support_empty_queries(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    # Intentionally leave nodes/edges parquet absent.
    clear_stores()
    store = get_store(settings)
    slice_ = store.bracket_elements()
    assert slice_.nodes == []
    assert slice_.edges == []
    counts = store.graph_counts()
    assert counts["total_nodes"] == 0
    assert counts["total_edges"] == 0


def test_bracket_elements_on_real_build_if_present() -> None:
    """Optional smoke against a local full build (skipped when artifacts missing)."""
    settings = Settings()
    if not settings.nodes_path.exists() or not settings.edges_path.exists():
        pytest.skip("build/nodes.parquet and build/edges.parquet not present")
    slice_ = bracket_elements(settings)
    matches = [n for n in slice_.nodes if n["data"]["type"] == "MATCH"]
    headers = [n for n in slice_.nodes if n["data"]["type"] == "STAGE_HEADER"]
    assert len(matches) == 32
    assert len(headers) == 5
    assert len(slice_.edges) == 32
    assert {n["data"]["type"] for n in slice_.nodes} == {"MATCH", "STAGE_HEADER"}
    assert {e["data"]["edge_type"] for e in slice_.edges} == {"ADVANCES_TO"}
    assert all(n["data"].get("stage") for n in matches)
    assert all(n["data"].get("short_label") for n in matches)
    assert all("position" in n for n in slice_.nodes)
    assert [h["data"]["label"] for h in headers] == [
        "Round of 32",
        "Round of 16",
        "Quarterfinals",
        "Semifinals",
        "Final / 3rd",
    ]

    xs = {n["data"]["stage"]: n["position"]["x"] for n in matches}
    assert xs["Round of 32"] < xs["Round of 16"] < xs["Quarterfinals"]
    assert xs["Semifinals"] < xs["Final"]
    assert xs["Final"] == xs["Third Place"]

    finals = [n for n in matches if n["data"]["stage"] == "Final"]
    thirds = [n for n in matches if n["data"]["stage"] == "Third Place"]
    assert len(finals) == 1 and len(thirds) == 1
    assert thirds[0]["position"]["y"] > finals[0]["position"]["y"]
    assert headers[0]["position"]["y"] < min(n["position"]["y"] for n in matches)
    if settings.odds_history_path.exists():
        with_odds = [n for n in matches if n["data"].get("odds_series")]
        assert len(with_odds) == 32
        assert all("current_home_prob" in n["data"] for n in with_odds)
        assert all(n["data"].get("winner_team") for n in with_odds)
    graph = rx.PyDiGraph()
    index: dict[str, int] = {}
    for node in matches:
        node_id = node["data"]["id"]
        index[node_id] = graph.add_node(node_id)
    for edge in slice_.edges:
        graph.add_edge(
            index[edge["data"]["source"]],
            index[edge["data"]["target"]],
            edge["data"]["edge_type"],
        )
    assert rx.is_directed_acyclic_graph(graph)


def test_bracket_elements_enrich_odds_series(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from oddsgraph.odds_history import ODDS_HISTORY_SCHEMA

    settings = make_settings(tmp_path)
    _write_fixture_graph(settings.build_dir)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "match_canonical_id": "match:brazil-vs-france",
                    "home_team": "Brazil",
                    "away_team": "France",
                    "odds_hour_epoch": 1000,
                    "home_prob": 0.55,
                    "away_prob": 0.45,
                    "match_start_epoch": 1000,
                    "match_end_epoch": 2000,
                    "winner_team": "Brazil",
                }
            ],
            schema=ODDS_HISTORY_SCHEMA,
        ),
        settings.odds_history_path,
    )

    slice_ = bracket_elements(settings)
    by_id = {el["data"]["id"]: el["data"] for el in slice_.nodes}
    match = by_id["match:brazil-vs-france"]
    assert match["home_team"] == "Brazil"
    assert match["away_team"] == "France"
    assert match["winner_team"] == "Brazil"
    assert match["odds_series"] == [{"h": 1000, "home": 0.55, "away": 0.45}]
    assert match["current_home_prob"] == 0.55
    assert "odds_series" not in by_id["match:final"]
