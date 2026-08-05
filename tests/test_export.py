from pathlib import Path

from oddsfox_graph.config import Settings
from oddsfox_graph.export import export_graph_artifacts
from oddsfox_graph.graphbuild import build_graph_from_fragments
from oddsfox_graph.reporting import build_inference_report
from oddsfox_graph.resolution import resolve_fragments
from oddsfox_graph.schema import CanonicalEdge, CanonicalNode, InferenceReport

from tests.helpers import load_fixture_fragment


def test_export_writes_parquet_files(tmp_path: Path) -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    report = build_inference_report(state, result)

    nodes_path = tmp_path / "nodes.parquet"
    edges_path = tmp_path / "edges.parquet"
    rejected_path = tmp_path / "rejected_edges.parquet"
    unresolved_path = tmp_path / "unresolved_entities.parquet"
    ontology_path = tmp_path / "ontology.json"
    report_path = tmp_path / "inference_report.json"

    export_graph_artifacts(
        nodes=result.nodes,
        edges=result.edges,
        rejected_edges=result.rejected_edges,
        unresolved=state.unresolved,
        report=report,
        nodes_path=nodes_path,
        edges_path=edges_path,
        rejected_edges_path=rejected_path,
        unresolved_entities_path=unresolved_path,
        ontology_path=ontology_path,
        inference_report_path=report_path,
    )

    assert nodes_path.exists()
    assert edges_path.exists()
    assert ontology_path.exists()
    assert report_path.exists()
