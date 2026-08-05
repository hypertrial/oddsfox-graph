from pathlib import Path

from oddsfox_graph.config import Settings
from oddsfox_graph.deterministic import build_deterministic_fragments_by_event
from oddsfox_graph.export import export_graph_artifacts
from oddsfox_graph.graphbuild import build_graph_from_fragments, validate_exported_graph
from oddsfox_graph.reporting import build_inference_report
from oddsfox_graph.resolution import resolve_fragments

from tests.helpers import load_fixture_fragment, load_golden_markets


def test_mocked_pipeline_snapshot(tmp_path: Path) -> None:
    markets = load_golden_markets()
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.ensure_dirs()

    deterministic = build_deterministic_fragments_by_event(markets)
    inferred = {
        "351746": load_fixture_fragment("351746"),
        "98266": load_fixture_fragment("98266"),
        "654708": load_fixture_fragment("654708"),
    }

    det_fragments = list(deterministic.values())
    inf_fragments = list(inferred.values())

    state_det = resolve_fragments(det_fragments, settings, inference_method="deterministic")
    state_inf = resolve_fragments(inf_fragments, settings, inference_method="llm")

    merged = state_det
    merged.canonical_nodes.update(state_inf.canonical_nodes)
    merged.local_to_canonical.update(state_inf.local_to_canonical)
    merged.unresolved.extend(state_inf.unresolved)

    all_fragments = det_fragments + inf_fragments
    graph = build_graph_from_fragments(all_fragments, merged, settings)
    report = build_inference_report(merged, graph)

    export_graph_artifacts(
        nodes=graph.nodes,
        edges=graph.edges,
        rejected_edges=graph.rejected_edges,
        unresolved=merged.unresolved,
        report=report,
        nodes_path=tmp_path / "nodes.parquet",
        edges_path=tmp_path / "edges.parquet",
        rejected_edges_path=tmp_path / "rejected_edges.parquet",
        unresolved_entities_path=tmp_path / "unresolved_entities.parquet",
        ontology_path=tmp_path / "ontology.json",
        inference_report_path=tmp_path / "inference_report.json",
    )

    errors = validate_exported_graph(graph.nodes, graph.edges)
    assert errors == []
    assert len(graph.nodes) > 20
    assert len(graph.edges) > 10
    assert report.node_counts.get("MARKET", 0) > 0
    assert report.node_counts.get("OUTCOME", 0) > 0
