import duckdb
import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.export import export_graph_artifacts
from oddsgraph.graphbuild import build_graph_from_fragments
from oddsgraph.reporting import build_inference_report
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import InferenceReport

from tests.helpers import load_fixture_fragment


def test_export_writes_parquet_files(tmp_path) -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    result = build_graph_from_fragments([fragment], state, settings)
    report = build_inference_report(state, result)

    nodes_path = tmp_path / "nodes.parquet"
    edges_path = tmp_path / "edges.parquet"
    rejected_path = tmp_path / "rejected_edges.parquet"
    ontology_path = tmp_path / "ontology.json"
    report_path = tmp_path / "inference_report.json"

    export_graph_artifacts(
        nodes=result.nodes,
        edges=result.edges,
        rejected_edges=result.rejected_edges,
        report=report,
        nodes_path=nodes_path,
        edges_path=edges_path,
        rejected_edges_path=rejected_path,
        ontology_path=ontology_path,
        inference_report_path=report_path,
    )

    assert nodes_path.exists()
    assert edges_path.exists()
    assert ontology_path.exists()
    assert report_path.exists()


def test_empty_exports_are_readable_by_duckdb(tmp_path) -> None:
    export_graph_artifacts(
        nodes=[],
        edges=[],
        rejected_edges=[],
        report=InferenceReport(),
        nodes_path=tmp_path / "nodes.parquet",
        edges_path=tmp_path / "edges.parquet",
        rejected_edges_path=tmp_path / "rejected.parquet",
        ontology_path=tmp_path / "ontology.json",
        inference_report_path=tmp_path / "report.json",
    )

    nodes_path = tmp_path / "nodes.parquet"
    table = pq.read_table(nodes_path)
    assert table.num_rows == 0
    assert "canonical_id" in table.column_names
    assert "proposition_json" in table.column_names

    edges_path = tmp_path / "edges.parquet"
    edges_table = pq.read_table(edges_path)
    assert "derivation_type" in edges_table.column_names
    assert "rule_id" in edges_table.column_names
    assert "premises" in edges_table.column_names

    count = duckdb.sql(
        f"SELECT count(*) FROM read_parquet('{nodes_path}')"
    ).fetchone()[0]
    assert count == 0


def test_export_serializes_proposition_json(tmp_path) -> None:
    from oddsgraph.ontology import NodeType
    from oddsgraph.schema import CanonicalNode, Proposition

    node = CanonicalNode(
        canonical_id="outcome:1:yes",
        type=NodeType.OUTCOME,
        label="Yes",
        confidence=1.0,
        evidence_market_ids=["1"],
        proposition=Proposition(
            predicate="wins_match",
            arguments={"team": "team:brazil", "match": "match:x"},
        ),
    )
    export_graph_artifacts(
        nodes=[node],
        edges=[],
        rejected_edges=[],
        report=InferenceReport(),
        nodes_path=tmp_path / "nodes.parquet",
        edges_path=tmp_path / "edges.parquet",
        rejected_edges_path=tmp_path / "rejected.parquet",
        ontology_path=tmp_path / "ontology.json",
        inference_report_path=tmp_path / "report.json",
    )
    rows = pq.read_table(tmp_path / "nodes.parquet").to_pylist()
    assert rows[0]["proposition_json"] is not None
    assert "wins_match" in rows[0]["proposition_json"]
