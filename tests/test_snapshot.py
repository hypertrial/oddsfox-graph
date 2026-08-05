from pathlib import Path

from oddsfox_graph.config import Settings
from oddsfox_graph.export import export_graph_artifacts
from oddsfox_graph.pipeline import build_pipeline_from_markets, validate_exported_artifacts

from tests.helpers import load_fixture_fragment, load_golden_markets


def test_mocked_pipeline_snapshot(tmp_path: Path) -> None:
    markets = load_golden_markets()
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()

    inferred = {
        "351746": load_fixture_fragment("351746"),
        "98266": load_fixture_fragment("98266"),
        "654708": load_fixture_fragment("654708"),
    }

    result = build_pipeline_from_markets(settings, markets, inferred)

    export_graph_artifacts(
        nodes=result.graph.nodes,
        edges=result.graph.edges,
        rejected_edges=result.graph.rejected_edges,
        report=result.report,
        nodes_path=settings.nodes_path,
        edges_path=settings.edges_path,
        rejected_edges_path=settings.rejected_edges_path,
        ontology_path=settings.ontology_path,
        inference_report_path=settings.inference_report_path,
    )

    errors = validate_exported_artifacts(settings)
    assert errors == []
    assert len(result.graph.nodes) > 20
    assert len(result.graph.edges) > 10
    assert result.report.node_counts.get("MARKET", 0) > 0
    assert result.report.node_counts.get("OUTCOME", 0) > 0
