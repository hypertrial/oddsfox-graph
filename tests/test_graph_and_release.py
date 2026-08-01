from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oddsfox_graph import __version__
from oddsfox_graph._discovery.contracts import (
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_VERIFIER_MODEL,
)
from oddsfox_graph._discovery.provenance import canonical_json_sha256, sha256_file
from oddsfox_graph._discovery import versions as discovery_versions
from oddsfox_graph._discovery.versions import (
    EXTRACTOR_VERSION,
    PUBLICATION_VERSION,
    RELEASE_FIXTURE_SCHEMA_VERSION,
    RULE_VERSION,
    VIEWER_API_VERSION,
    VIEWER_ARTIFACT_VERSION,
    WC2026_SOURCE_SCHEMA,
    discovery_semantics_fingerprint,
)
from oddsfox_graph._explorer.human import (
    _classification_coverage,
    essential_relationship_rows,
)
from oddsfox_graph._explorer.aggregation import EVENT_SUMMARY_COLUMNS
from oddsfox_graph.graph import Graph
from oddsfox_graph.explorer import (
    create_explorer_app,
    export_explorer,
    validate_explorer_host,
)
from oddsfox_graph.queries import DuckDB, q
from oddsfox_graph import release_validation
from oddsfox_graph import operability
from oddsfox_graph.operability import doctor
from oddsfox_graph.release_validation import validate_release_fixture


def test_graph_proofs_use_only_implication_and_equivalence(tmp_path: Path) -> None:
    out = _write_graph(tmp_path)
    graph = Graph.open(out)
    proofs = graph.prove("a", "d", max_hops=4, max_paths=3)
    assert len(proofs) == 2
    assert proofs[0].hops == 2
    assert proofs[0].bottleneck_confidence == 0.8
    assert all(
        step.edge_type in {"implies", "equivalent"}
        for proof in proofs
        for step in proof.steps
    )
    reverse = graph.prove("c", "b", max_hops=1)
    assert reverse and reverse[0].steps[0].edge_type == "equivalent"
    assert graph.prove("d", "a", max_hops=4) == ()


def test_graph_proof_ordering_and_limits_are_deterministic(tmp_path: Path) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    first = graph.prove("a", "d", max_hops=4, max_paths=1)
    second = graph.prove("a", "d", max_hops=4, max_paths=1)
    assert first == second
    assert len(first) == 1
    with pytest.raises(ValueError):
        graph.prove("a", "d", max_hops=0)
    with pytest.raises(ValueError, match="max_hops"):
        graph.prove("a", "d", max_hops=9)
    with pytest.raises(ValueError, match="max_paths"):
        graph.prove("a", "d", max_paths=21)


def test_why_not_reports_accepted_rejected_quarantine_and_not_retrieved(tmp_path: Path) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    assert graph.why_not("a", "b", "implies").status == "accepted"
    assert graph.why_not("a", "d", "compatible").status == "solver_rejected"
    assert graph.why_not("b", "d", "compatible").status == "model_disagreement"
    assert graph.why_not("a", "c", "compatible").status == "below_threshold"
    assert graph.why_not("c", "d", "compatible").status == "not_retrieved"
    assert graph.why_not("a", "d", "implies").status == "not_retrieved"
    assert graph.why_not("d", "a", "implies").status == "solver_rejected"
    assert graph.why_not("missing", "d", "compatible").status == "unknown_node"


@pytest.mark.parametrize(
    ("reason", "stage", "expected"),
    (
        ("assumption", "classification", "assumption"),
        ("invalid_citation", "classification", "invalid_citation"),
        ("nli_veto", "classification", "nli_veto"),
        ("inference_failure", "classification", "inference_failure"),
        ("authoritative_conflict", "parse", "quarantined_parse"),
    ),
)
def test_why_not_maps_every_quarantine_category(
    tmp_path: Path,
    reason: str,
    stage: str,
    expected: str,
) -> None:
    graph = Graph.open(
        _write_graph(
            tmp_path / reason,
            quarantine_reason=reason,
            quarantine_stage=stage,
        )
    )
    assert graph.why_not("b", "d", "compatible").status == expected


def test_graph_search_nodes_edges_condition_and_explanations(tmp_path: Path) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    assert graph.search("Alpha")[0].node_id == "a"
    assert len(graph.nodes()) == 4
    assert graph.edges("implies")[0].src_node_id == "a"
    assert graph.condition("a", "b")[0]["method"] == "logic"
    assert graph.explain_node("a")["edges"]
    assert graph.explain_edge("a", "b", "implies")["edges"]


def test_explorer_api_is_loopback_bounded_and_cacheable(tmp_path: Path) -> None:
    out = _write_graph(tmp_path)
    assert validate_explorer_host("127.0.0.1") == "127.0.0.1"
    assert validate_explorer_host("::1") == "::1"
    assert validate_explorer_host("[::1]") == "::1"
    with pytest.raises(ValueError, match="loopback"):
        validate_explorer_host("0.0.0.0")

    client = TestClient(
        create_explorer_app(out, max_response_nodes=4, max_response_edges=5)
    )
    metadata = client.get("/api/v1/meta")
    assert metadata.status_code == 200
    assert metadata.json()["package_version"] == __version__
    assert metadata.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in metadata.headers["content-security-policy"]
    assert client.get("/api/v1/overview?level=event").json()["nodes"][0]["id"] == "e"
    assert client.get("/api/v1/subgraph?node=a&hops=1").status_code == 200
    assert client.get("/api/v1/subgraph?node=a&max_nodes=5").status_code == 422
    bounded_node = TestClient(
        create_explorer_app(out, max_response_nodes=4, max_response_edges=2)
    ).get("/api/v1/nodes/b")
    assert bounded_node.status_code == 200
    assert len(bounded_node.json()["edges"]) == 2
    assert bounded_node.json()["edges_truncated"] is True
    etag = metadata.headers["etag"]
    assert client.get("/api/v1/meta", headers={"If-None-Match": etag}).status_code == 304

    manifest_path = out / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_marker"] = "changed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed_client = TestClient(
        create_explorer_app(out, max_response_nodes=4, max_response_edges=5)
    )
    assert changed_client.get("/api/v1/meta").headers["etag"] != etag


def test_recording_plan_is_deterministic_auditable_and_bounded(
    tmp_path: Path,
) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    first = graph.recording_plan(limit=6, min_confidence=0.95)
    second = graph.recording_plan(limit=6, min_confidence=0.95)
    assert first == second
    assert first.schema_version == "oddsfox-recording-plan-v2"
    assert first.ranking_version == "human-wc2026-story-edge-v2"
    assert first.graph_fingerprint == "fixture"
    assert first.mode == "full"
    assert first.requested_limit == 6
    assert first.eligible_edge_count == 2
    assert [item.proposal_id for item in first.highlights] == ["p4"]
    assert first.highlights[0].score_breakdown.base_importance == pytest.approx(
        0.9475
    )
    assert first.highlights[0].source_plain_claim == (
        "Argentina wins the World Cup"
    )
    assert {edge.id for edge in first.graph.edges} >= {"p4"}
    assert first.context_pruning.retained_nodes <= 96
    assert first.context_pruning.retained_edges <= 144
    assert first.context_pruning.pruned_edges == 0

    with pytest.raises(ValueError, match="between 1 and 12"):
        graph.recording_plan(limit=13)

    empty_out = _write_graph(tmp_path / "empty")
    empty_db = DuckDB(empty_out / "oddsfox_graph.duckdb")
    try:
        empty_db.execute("DELETE FROM edges")
    finally:
        empty_db.close()
    with pytest.raises(ValueError, match="No accepted"):
        Graph.open(empty_out).recording_plan(min_confidence=0.95, limit=1)


def test_recording_plan_api_validates_limits_and_serializes(tmp_path: Path) -> None:
    client = TestClient(create_explorer_app(_write_graph(tmp_path)))
    response = client.get(
        "/api/v1/recording-plan?limit=2&min_confidence=0.95"
    )
    assert response.status_code == 200
    assert response.json()["highlights"][0]["proposal_id"] == "p4"
    assert client.get("/api/v1/recording-plan?limit=0").status_code == 422
    assert (
        client.get("/api/v1/recording-plan?min_confidence=1.1").status_code
        == 422
    )


def test_wc2026_human_explorer_routes_use_structured_claims(tmp_path: Path) -> None:
    out = _write_graph(tmp_path)
    graph = Graph.open(out)
    home = graph.explore_home(team_limit=4, highlight_limit=2)
    assert home.scope.scope == "wc2026"
    assert [team.canonical_team_name for team in home.teams] == [
        "Argentina",
        "Brazil",
        "France",
        "Germany",
    ]
    assert home.notable_relationships[0].relationship.source.plain_claim == (
        "Argentina wins the World Cup"
    )
    assert home.display_stats.input_edge_count == 5
    assert home.display_stats.display_edge_count == 4
    assert home.display_stats.omitted_edge_count == 1
    assert graph.team("argentina").summary.max_stage_rank == 5
    argentina = graph.team("argentina").summary
    assert argentina.classification_eligible_count == 1
    assert argentina.classification_assessed_count == 1
    assert argentina.classification_status == "complete"
    assert argentina.classification_coverage == 1.0
    winner = graph.stage("winner").summary
    assert winner.label == "World Cup winner"
    assert winner.classification_status == "complete"
    assert graph.stage("final").summary.classification_status == "not_applicable"
    assert graph.market("m1").claims[0].plain_claim == (
        "Argentina wins the World Cup"
    )
    comparison = graph.compare("a", "d")
    assert comparison.status == "direct"
    assert comparison.direct is not None
    assert comparison.direct.proposal_id == "p4"
    search_result = graph.entity_search("Argentina")[0]
    assert search_result.kind == "team"
    assert search_result.description == "1 progression market"

    client = TestClient(create_explorer_app(out))
    assert client.get("/api/v1/explore").status_code == 200
    assert client.get("/api/v1/teams/argentina").status_code == 200
    assert client.get("/api/v1/stages/winner").status_code == 200
    assert client.get("/api/v1/markets/m1").status_code == 200
    assert client.get("/api/v1/relationships/p4").status_code == 200
    assert client.get("/api/v1/entity-search?q=Argentina").status_code == 200
    assert client.get("/api/v1/compare?a=a&b=d").json()["status"] == "direct"

    indirect = graph.compare("b", "d")
    assert indirect.status == "path"
    assert [item.proposal_id for item in indirect.path] == ["p2", "p3"]
    assert graph.relationship("p5").proposal_id == "p5"


def test_human_essential_projection_is_deterministic_and_preservable() -> None:
    def edge(
        proposal_id: str,
        source: str,
        target: str,
        relation: str,
        confidence: float,
    ) -> dict[str, object]:
        return {
            "proposal_id": proposal_id,
            "src_node_id": source,
            "dst_node_id": target,
            "edge_type": relation,
            "confidence": confidence,
        }

    rows = [
        edge("path-a", "a", "b", "implies", 0.95),
        edge("path-b", "b", "c", "implies", 0.95),
        edge("redundant", "a", "c", "implies", 0.95),
        edge("strong-direct", "a", "d", "implies", 0.96),
        edge("weak-path-a", "a", "e", "implies", 0.95),
        edge("weak-path-b", "e", "d", "implies", 0.95),
        edge("source-complement", "x", "y", "complement", 1.0),
        edge("duplicate-later", "y", "x", "complement", 1.0),
    ]
    projected = essential_relationship_rows(rows)
    assert [row["proposal_id"] for row in projected] == [
        "duplicate-later",
        "strong-direct",
        "path-a",
        "weak-path-a",
        "path-b",
        "weak-path-b",
    ]
    assert projected == essential_relationship_rows(reversed(rows))
    preserved = essential_relationship_rows(
        rows,
        preserve_proposal_ids=frozenset({"redundant"}),
    )
    assert "redundant" in {row["proposal_id"] for row in preserved}


@pytest.mark.parametrize(
    ("eligible", "assessed", "status", "coverage"),
    (
        (0, 0, "not_applicable", None),
        (3, 0, "not_started", 0.0),
        (4, 1, "partial", 0.25),
        (4, 4, "complete", 1.0),
    ),
)
def test_human_coverage_uses_four_explicit_states(
    eligible: int,
    assessed: int,
    status: str,
    coverage: float | None,
) -> None:
    assert _classification_coverage(eligible, assessed) == (
        eligible,
        assessed,
        status,
        coverage,
    )


def test_essential_edge_mode_removes_only_proven_redundancy(tmp_path: Path) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    complete = graph.neighborhood(
        ("a", "b", "c", "d"),
        hops=1,
        max_nodes=4,
        max_edges=10,
    )
    essential = graph.neighborhood(
        ("a", "b", "c", "d"),
        hops=1,
        max_nodes=4,
        max_edges=10,
        edge_mode="essential",
    )
    assert {edge.id for edge in complete.edges} == {"p1", "p2", "p3", "p4", "p5"}
    assert {edge.id for edge in essential.edges} == {"p1", "p2", "p3", "p4"}
    assert essential.display_stats is not None
    assert essential.display_stats.omitted_edge_count == 1


def test_graph_open_rejects_stale_semantics_fingerprint(tmp_path: Path) -> None:
    out = _write_graph(tmp_path)
    manifest_path = out / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["discovery_semantics_fingerprint"] = "stale"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="semantics fingerprint"):
        Graph.open(out)


def test_discovery_semantics_fingerprint_binds_aggregation_and_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = discovery_semantics_fingerprint()
    with monkeypatch.context() as patch:
        patch.setattr(
            discovery_versions,
            "AGGREGATION_CONTRACT_VERSION",
            "explorer-aggregation-audit-change",
        )
        assert discovery_semantics_fingerprint() != baseline
    with monkeypatch.context() as patch:
        patch.setattr(
            discovery_versions,
            "WC2026_QUALIFICATION_GENERATOR_VERSION",
            "wc2026-catalog-qualification-audit-change",
        )
        assert discovery_semantics_fingerprint() != baseline
    with monkeypatch.context() as patch:
        patch.setitem(EVENT_SUMMARY_COLUMNS, "audit_contract_field", "VARCHAR")
        assert discovery_semantics_fingerprint() != baseline


def test_recording_rejects_non_wc2026_graph_before_runtime_preflight(
    tmp_path: Path,
) -> None:
    out = _write_graph(tmp_path)
    build_path = out / "build_manifest.json"
    viewer_path = out / "viewer_manifest.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    viewer = json.loads(viewer_path.read_text(encoding="utf-8"))
    build["input_schema"] = "canonical-propositions-v1"
    viewer["input_profile"] = "canonical-propositions-v1"
    build_path.write_text(json.dumps(build), encoding="utf-8")
    viewer_path.write_text(json.dumps(viewer), encoding="utf-8")

    graph = Graph.open(out)
    with pytest.raises(ValueError, match="polymarket-wc2026-graph-hourly-v1"):
        graph.recording_plan()


def test_component_semantic_zoom_uses_a_component_scoped_event_view(
    tmp_path: Path,
) -> None:
    client = TestClient(create_explorer_app(_write_graph(tmp_path)))
    response = client.get("/api/v1/component-graph/component-one")
    assert response.status_code == 200
    assert response.json()["level"] == "event"
    assert [node["id"] for node in response.json()["nodes"]] == ["e"]
    assert client.get("/api/v1/component-graph/missing").status_code == 404


def test_event_overview_reports_edge_truncation_at_zero_limit(
    tmp_path: Path,
) -> None:
    view = Graph.open(_write_graph(tmp_path)).overview("event", max_edges=0)
    assert view.edges == ()
    assert view.truncated_edges is True


def test_recording_plan_collapses_reversed_symmetric_duplicates(
    tmp_path: Path,
) -> None:
    out = _write_graph(tmp_path)
    db = DuckDB(out / "oddsfox_graph.duckdb")
    try:
        db.execute(
            """
            INSERT INTO edges
            SELECT * REPLACE (
                dst_node_id AS src_node_id,
                src_node_id AS dst_node_id,
                'p6' AS proposal_id
            )
            FROM edges
            WHERE proposal_id = 'p4'
            """
        )
    finally:
        db.close()
    plan = Graph.open(out).recording_plan(limit=6, min_confidence=0.95)
    assert plan.eligible_edge_count == 2
    assert [item.proposal_id for item in plan.highlights] == ["p4"]


def test_neighborhood_enforces_seed_ceiling_without_false_truncation(
    tmp_path: Path,
) -> None:
    graph = Graph.open(_write_graph(tmp_path))
    complete = graph.neighborhood(
        ("a", "b", "c", "d"),
        hops=1,
        max_nodes=4,
        max_edges=5,
    )
    assert complete.truncated_nodes is False
    assert complete.truncated_edges is False
    assert {node.id for node in complete.nodes} == {"a", "b", "c", "d"}
    assert all(
        edge.source in {node.id for node in complete.nodes}
        and edge.target in {node.id for node in complete.nodes}
        for edge in complete.edges
    )
    with pytest.raises(ValueError, match="Seed node count"):
        graph.neighborhood(
            ("a", "b", "c", "d"),
            hops=1,
            max_nodes=3,
        )


def test_static_explorer_export_contains_bounded_parquet_snapshot(tmp_path: Path) -> None:
    out = _write_graph(tmp_path / "source")
    destination = tmp_path / "export"
    manifest = export_explorer(
        out,
        destination,
        scope="event",
        identifier="e",
        max_nodes=4,
        max_edges=5,
    )
    assert manifest["data_format"] == "duckdb-wasm-parquet"
    assert manifest["schema_version"] == "static-explorer-v3"
    assert manifest["graph_content_fingerprint"] == "fixture"
    assert manifest["build_mode"] == "full"
    assert manifest["validation_status"] == "EXPERIMENTAL_FULL"
    assert manifest["input_profile"] == WC2026_SOURCE_SCHEMA
    assert manifest["source_file_sha256"] == "fixture-source"
    assert (
        manifest["normalized_semantic_fingerprint"]
        == "fixture-input-semantics"
    )
    assert (
        manifest["discovery_semantics_fingerprint"]
        == discovery_semantics_fingerprint()
    )
    assert manifest["source_tree_fingerprint"] == "fixture-source-tree"
    assert manifest["display_stats"] == {
        "input_node_count": 4,
        "input_edge_count": 5,
        "display_node_count": 4,
        "display_edge_count": 4,
        "omitted_edge_count": 1,
        "density": 1 / 3,
        "label_uniqueness": 1.0,
        "max_degree": 2,
        "recommended_representation": "grouped",
    }
    assert set(manifest["snapshot_files"]) == {
        "snapshot_nodes.parquet",
        "snapshot_edges.parquet",
        "snapshot_stages.parquet",
        "snapshot_teams.parquet",
        "snapshot_markets.parquet",
        "snapshot_claims.parquet",
        "snapshot_relationships.parquet",
        "snapshot_relationship_groups.parquet",
    }
    for relative in (
        "index.html",
        "static_manifest.json",
        "snapshot_nodes.parquet",
        "snapshot_edges.parquet",
    ):
        assert (destination / relative).is_file()
    db = DuckDB()
    try:
        assert db.rows(
            f"SELECT count(*) AS count FROM read_parquet('{q(destination / 'snapshot_nodes.parquet')}')"
        )[0]["count"] == 4
        assert db.rows(
            f"SELECT DISTINCT evidence_tier FROM read_parquet('{q(destination / 'snapshot_edges.parquet')}') ORDER BY evidence_tier"
        )
        assert {
            row["id"]
            for row in db.rows(
                f"SELECT id FROM read_parquet('{q(destination / 'snapshot_edges.parquet')}')"
            )
        } == {"p1", "p2", "p3", "p4", "p5"}
        assert {
            row["proposal_id"]
            for row in db.rows(
                f"SELECT proposal_id FROM read_parquet('{q(destination / 'snapshot_relationships.parquet')}')"
            )
        } == {"p1", "p2", "p3", "p4"}
        stage_coverage = {
            row["stage_key"]: row
            for row in db.rows(
                f"SELECT stage_key, classification_eligible_count, "
                "classification_assessed_count, classification_status, "
                f"classification_coverage FROM read_parquet('{q(destination / 'snapshot_stages.parquet')}')"
            )
        }
        assert stage_coverage["winner"] == {
            "stage_key": "winner",
            "classification_eligible_count": 1,
            "classification_assessed_count": 1,
            "classification_status": "complete",
            "classification_coverage": 1.0,
        }
        assert stage_coverage["final"]["classification_status"] == "not_applicable"
        assert stage_coverage["final"]["classification_coverage"] is None
    finally:
        db.close()

    component_destination = tmp_path / "component-export"
    export_explorer(
        out,
        component_destination,
        scope="component",
        identifier="component-one",
        max_nodes=4,
        max_edges=5,
    )
    with pytest.raises(KeyError, match="Unknown event_key"):
        export_explorer(
            out,
            tmp_path / "missing-export",
            scope="event",
            identifier="missing",
        )
    with pytest.raises(ValueError, match="must not overlap"):
        export_explorer(
            out,
            out / "static-export",
            scope="event",
            identifier="e",
        )


def test_release_fixture_validates_fast_hashes_counts_and_performance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "input.parquet").write_bytes(b"canonical-test-catalog")
    source_hash = sha256_file(root / "input.parquet")
    monkeypatch.setattr(release_validation, "CANONICAL_CATALOG_SHA256", source_hash)

    baseline = root / "baselines" / "fast"
    baseline.mkdir(parents=True)
    hashes = {"nodes.parquet": "logical-nodes-hash"}
    (baseline / "build_manifest.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "build_mode": "fast",
                "validation_status": "DETERMINISTIC_VALIDATED",
                "input": {"sha256": source_hash},
                "stats": {
                    "same_market_complement_edges": 94_771,
                    "same_market_categorical_exclusion_edges": 54,
                    "cross_market_deterministic_edges": 10,
                    "cross_event_deterministic_edges": 2,
                },
                "deadline": {"met": True},
                "artifact_hashes": hashes,
            }
        ),
        encoding="utf-8",
    )
    (baseline / "coverage_summary.json").write_text(
        json.dumps(
            {
                "all_market_selection": True,
                "markets": 94_777,
                "propositions": 189_570,
                "input_selection": {
                    "input_market_rows": 94_781,
                    "invalid_market_rows": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    (baseline / "viewer_manifest.json").write_text(
        json.dumps(
            {
                "build_mode": "fast",
                "validation_status": "DETERMINISTIC_VALIDATED",
                "graph_content_fingerprint": "fixture",
            }
        ),
        encoding="utf-8",
    )
    (root / "expected_artifact_hashes.json").write_text(
        json.dumps({"fast": hashes}), encoding="utf-8"
    )
    (root / "performance_report.json").write_text(
        json.dumps(
            {
                "schema_version": "performance-budget-v3",
                "input_sha256": source_hash,
                "hardware": {
                    "system": "Darwin",
                    "machine": "arm64",
                    "processor": "Apple M4",
                },
                "budget": {
                    "schema_version": "performance-budget-v3",
                    "input_sha256": source_hash,
                    "system": "Darwin",
                    "machine": "arm64",
                    "processor_contains": "Apple M4",
                    "repetitions": 3,
                    "selection": "complete-valid-catalog",
                    "extractor_version": EXTRACTOR_VERSION,
                    "rule_version": RULE_VERSION,
                },
                "acceptance": {
                    "every_run_ready_within_budget": True,
                    "every_discovery_deadline_met": True,
                    "logical_hashes_identical": True,
                    "inference_resources_absent": True,
                },
                "passed": True,
                "runs": [
                    {
                        "time_to_ready_seconds": 90.0 + index,
                        "deadline_met": True,
                        "logical_artifact_hashes": hashes,
                    }
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    bound = [
        "input.parquet",
        "performance_report.json",
        "expected_artifact_hashes.json",
        "baselines/fast/build_manifest.json",
        "baselines/fast/viewer_manifest.json",
        "baselines/fast/coverage_summary.json",
    ]
    fixture = {
        "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
        "package_version": __version__,
        "source_sha256": source_hash,
        "files": {name: sha256_file(root / name) for name in bound},
        "trees": {
            "baselines/fast": _tree_binding(root / "baselines" / "fast")
        },
    }
    (root / "release-fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    result = validate_release_fixture(root, tmp_path / "work")
    assert result["passed"] is True
    assert result["decision"] == "DETERMINISTIC_VALIDATED"

    performance_path = root / "performance_report.json"
    performance = json.loads(performance_path.read_text(encoding="utf-8"))
    performance["runs"][0]["logical_artifact_hashes"] = {
        "nodes.parquet": "different-logical-hash"
    }
    performance_path.write_text(json.dumps(performance), encoding="utf-8")
    fixture["files"]["performance_report.json"] = sha256_file(performance_path)
    (root / "release-fixture.json").write_text(
        json.dumps(fixture), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not match the release baseline"):
        validate_release_fixture(root, tmp_path / "work-mismatch")


def test_release_fixture_rejects_path_traversal_and_wrong_status(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "release-fixture.json").write_text(
        json.dumps(
                {
                    "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
                    "package_version": __version__,
                    "source_sha256": release_validation.CANONICAL_CATALOG_SHA256,
                    "files": {"../escape": "0" * 64},
                    "trees": {},
                }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unsafe"):
        validate_release_fixture(root, tmp_path / "work")


def test_doctor_reports_nonfatal_warnings_and_required_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE catalog AS SELECT
                'm1'::VARCHAR AS market_id,
                'Will Alpha happen?'::VARCHAR AS question,
                ['Yes','No']::VARCHAR[] AS outcomes,
                ['yes','no']::VARCHAR[] AS clob_token_ids
            """
        )
        db.execute(f"COPY catalog TO '{q(catalog)}' (FORMAT PARQUET)")
    finally:
        db.close()
    primary = _write_model_manifest(
        tmp_path / "primary.json", DEFAULT_PRIMARY_MODEL, 8080
    )
    verifier = _write_model_manifest(
        tmp_path / "verifier.json", DEFAULT_VERIFIER_MODEL, 8081
    )
    compute = tmp_path / "compute.json"
    compute.write_text(
        json.dumps({"hardware_hour_usd": 1.0, "currency": "USD"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(operability, "check_model", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(operability.importlib.util, "find_spec", lambda _name: object())
    fast_report = doctor(
        catalog,
        tmp_path / "fast-out",
        "fast",
        None,
        None,
        None,
        None,
        "http://127.0.0.1:8080/v1",
        "http://127.0.0.1:8081/v1",
        None,
    )
    assert fast_report.passed is True
    assert any(
        check.name == "fast_performance_budget" and check.status == "pass"
        for check in fast_report.checks
    )
    report = doctor(
        catalog,
        tmp_path / "out",
        "full",
        tmp_path / "cache",
        tmp_path / "profile.json",
        primary,
        verifier,
        "http://127.0.0.1:8080/v1",
        "http://127.0.0.1:8081/v1",
        compute,
    )
    assert report.passed is False
    assert any(
        check.name == "automation_profile" and check.status == "fail"
        for check in report.checks
    )
    compute.write_text("{}", encoding="utf-8")
    failed = doctor(
        catalog,
        tmp_path / "out",
        "full",
        tmp_path / "cache",
        tmp_path / "profile.json",
        primary,
        verifier,
        "http://127.0.0.1:8080/v1",
        "http://127.0.0.1:8081/v1",
        compute,
    )
    assert failed.passed is False
    assert any(
        check.name == "compute_profile" and check.status == "fail"
        for check in failed.checks
    )


def _write_graph(
    tmp_path: Path,
    *,
    quarantine_reason: str = "model_disagreement",
    quarantine_stage: str = "classification",
) -> Path:
    out = tmp_path / "out"
    out.mkdir(parents=True)
    db = DuckDB(out / "oddsfox_graph.duckdb")
    try:
        db.execute(
            """
            CREATE TABLE nodes AS SELECT * FROM (VALUES
                ('a','m1',0,'a','Alpha?','Yes','e',true,false,'unknown','Alpha','binary',2,NULL::TIMESTAMPTZ,NULL::TIMESTAMPTZ),
                ('b','m2',0,'b','Beta?','Yes','e',true,false,'unknown','Beta','binary',2,NULL::TIMESTAMPTZ,NULL::TIMESTAMPTZ),
                ('c','m3',0,'c','Gamma?','Yes','e',true,false,'unknown','Gamma','binary',2,NULL::TIMESTAMPTZ,NULL::TIMESTAMPTZ),
                ('d','m4',0,'d','Delta?','Yes','e',true,false,'unknown','Delta','binary',2,NULL::TIMESTAMPTZ,NULL::TIMESTAMPTZ)
            ) t(node_id,market_id,outcome_index,clob_token_id,question,outcome_label,event_slug,is_active,is_closed,market_family,canonical_proposition,proposition_type,expected_tokens,first_seen_ts,last_seen_ts)
            """
        )
        db.execute(
            """
            CREATE TABLE edges AS SELECT * FROM (VALUES
                ('a','b','implies','model',0.9,'m1','m2','e','e','a implies b','generative_consensus',NULL,'p','a implies b',[]::VARCHAR[],NULL,'p1','s','c','sc','pm','vm','pf','vf','cf','ap','generative_consensus',NULL,NULL,NULL,NULL,NULL),
                ('b','c','equivalent','model',0.8,'m2','m3','e','e','b equals c','generative_consensus',NULL,'p','b equals c',[]::VARCHAR[],NULL,'p2','s','c','sc','pm','vm','pf','vf','cf','ap','generative_consensus',NULL,NULL,NULL,NULL,NULL),
                ('c','d','implies','model',0.95,'m3','m4','e','e','c implies d','generative_consensus',NULL,'p','c implies d',[]::VARCHAR[],NULL,'p3','s','c','sc','pm','vm','pf','vf','cf','ap','generative_consensus',NULL,NULL,NULL,NULL,NULL),
                ('b','d','implies','model',0.8,'m2','m4','e','e','b implies d','generative_consensus',NULL,'p','b implies d',[]::VARCHAR[],NULL,'p5','s','c','sc','pm','vm','pf','vf','cf','ap','generative_consensus',NULL,NULL,NULL,NULL,NULL),
                ('a','d','mutually_exclusive','same_market',1.0,'m1','m4','e','e','exclusive','deterministic','v',NULL,'exclusive',[]::VARCHAR[],'rule','p4','s','c','sc',NULL,NULL,NULL,NULL,NULL,NULL,'source_contract','strict','v1','[]','rule-fingerprint','scope')
            ) t(src_node_id,dst_node_id,edge_type,edge_basis,confidence,market_id_src,market_id_dst,event_slug_src,event_slug_dst,evidence,discovery_method,rule_version,prompt_version,explanation,assumptions,rule_id,proposal_id,solver_version,constraint_version,solver_component_id,primary_model_version,verifier_model_version,primary_inference_fingerprint,verifier_inference_fingerprint,consensus_fingerprint,automation_profile_id,evidence_tier,extractor_id,extractor_version,source_spans_json,rule_applicability_fingerprint,proof_scope_key)
            """
        )
        db.execute("CREATE TABLE conditionals AS SELECT 'a' AS a_node_id, 'b' AS b_node_id, 1.0 AS p_a_given_b, 'logic' AS method, 0.9 AS confidence, 'proof' AS evidence")
        db.execute(
            """
            CREATE TABLE rejected AS SELECT * FROM (VALUES
                ('r1','a','d','compatible','conflict'),
                ('r2','d','a','implies','directional conflict')
            ) t(proposal_id,src_node_id,dst_node_id,edge_type,rejection_reason)
            """
        )
        quarantine_b = "NULL::VARCHAR" if quarantine_stage == "parse" else "'d'"
        db.execute(
            "CREATE TABLE quarantine AS SELECT "
            "'q' AS quarantine_id, 'b' AS proposition_a_id, "
            f"{quarantine_b} AS proposition_b_id, "
            f"'{quarantine_stage}' AS stage, "
            f"'{quarantine_reason}' AS reason_code, "
            "'diagnostic fixture' AS explanation"
        )
        db.execute(
            "CREATE TABLE candidates AS SELECT "
            "'a' AS proposition_a_id, 'c' AS proposition_b_id, "
            "['semantic']::VARCHAR[] AS candidate_reasons, "
            "NULL::VARCHAR AS deterministic_relation, "
            "'accepted'::VARCHAR AS status"
        )
        db.execute("CREATE VIEW nodes_table AS SELECT * FROM nodes")
        db.execute("CREATE VIEW logic_edges_v AS SELECT * FROM edges")
        db.execute("CREATE VIEW rejected_edges_v AS SELECT * FROM rejected")
        db.execute("CREATE VIEW quarantined_pairs_v AS SELECT * FROM quarantine")
        db.execute("CREATE VIEW relation_candidates_v AS SELECT * FROM candidates")
        db.execute(
            """
            CREATE TABLE explorer_propositions_v AS
            SELECT node_id AS proposition_id, market_id, question,
                   NULL::VARCHAR AS event_id, event_slug,
                   NULL::VARCHAR AS description, NULL::VARCHAR AS category,
                   []::VARCHAR[] AS tags, canonical_proposition,
                   'parsed'::VARCHAR AS parse_status,
                   'e'::VARCHAR AS event_key, 'sports'::VARCHAR AS primary_domain,
                   'component-one'::VARCHAR AS component_id,
                   'component-fingerprint'::VARCHAR AS component_fingerprint,
                   CASE node_id WHEN 'a' THEN 'Argentina'
                        WHEN 'b' THEN 'Brazil' WHEN 'c' THEN 'France'
                        ELSE 'Germany' END::VARCHAR AS team_name,
                   CASE node_id WHEN 'a' THEN 'winner'
                        WHEN 'b' THEN 'final' WHEN 'c' THEN 'semifinal'
                        ELSE 'quarterfinal' END::VARCHAR AS stage_key,
                   CASE node_id WHEN 'a' THEN 5 WHEN 'b' THEN 4
                        WHEN 'c' THEN 3 ELSE 2 END::BIGINT AS stage_rank,
                   CASE node_id WHEN 'a' THEN 5 WHEN 'b' THEN 4
                        WHEN 'c' THEN 3 ELSE 2 END::BIGINT AS progression_level,
                   'advance'::VARCHAR AS market_direction,
                   'Yes'::VARCHAR AS progression_outcome,
                   true::BOOLEAN AS is_progression,
                   'active'::VARCHAR AS market_status,
                   true::BOOLEAN AS is_still_alive,
                   NULL::VARCHAR AS opposite_clob_token_id,
                   NULL::DOUBLE AS market_volume_usd
            FROM nodes
            """
        )
        db.execute(
            """
            CREATE TABLE event_summary_v AS SELECT
                'e'::VARCHAR AS event_key, NULL::VARCHAR AS event_id,
                'e'::VARCHAR AS event_slug, 'Fixture event'::VARCHAR AS label,
                'sports'::VARCHAR AS primary_domain, NULL::VARCHAR AS category,
                4::BIGINT AS market_count, 4::BIGINT AS proposition_count,
                4::BIGINT AS active_market_count, 0::BIGINT AS closed_market_count,
                5::BIGINT AS accepted_edge_count, 2::BIGINT AS rejected_edge_count,
                1::BIGINT AS quarantined_pair_count, 0::BIGINT AS unclassified_pair_count,
                1::BIGINT AS classification_eligible_count,
                1::BIGINT AS classification_assessed_count,
                'complete'::VARCHAR AS classification_status,
                1.0::DOUBLE AS classification_coverage,
                1::BIGINT AS deterministic_edge_count, 4::BIGINT AS consensus_edge_count,
                0::BIGINT AS complement_count, 1::BIGINT AS equivalent_count,
                1::BIGINT AS mutually_exclusive_count, 3::BIGINT AS implies_count,
                0::BIGINT AS compatible_count, 1::BIGINT AS component_count,
                NULL::TIMESTAMPTZ AS first_seen_ts, NULL::TIMESTAMPTZ AS last_seen_ts
            """
        )
        db.execute(
            """
            CREATE TABLE event_relation_summary_v AS SELECT
                'e'::VARCHAR AS src_event_key, 'e'::VARCHAR AS dst_event_key,
                'implies'::VARCHAR AS edge_type, 3::BIGINT AS edge_count,
                'generative_consensus'::VARCHAR AS evidence_tier,
                0.8::DOUBLE AS min_confidence, 0.95::DOUBLE AS max_confidence,
                0.883::DOUBLE AS mean_confidence, 0::BIGINT AS deterministic_count,
                3::BIGINT AS consensus_count, 3::BIGINT AS source_market_count,
                3::BIGINT AS destination_market_count, true AS aggregation_only
            """
        )
        db.execute(
            """
            CREATE TABLE component_summary_v AS SELECT
                'component-one'::VARCHAR AS component_id,
                'component-fingerprint'::VARCHAR AS component_fingerprint,
                4::BIGINT AS proposition_count, 4::BIGINT AS market_count,
                1::BIGINT AS event_count, 5::BIGINT AS edge_count,
                1::BIGINT AS deterministic_edge_count, 4::BIGINT AS consensus_edge_count,
                1::BIGINT AS quarantined_pair_count, 0::BIGINT AS unclassified_pair_count,
                'complete'::VARCHAR AS classification_status,
                1.0::DOUBLE AS classification_coverage,
                ['a','b','c','d']::VARCHAR[] AS representative_node_ids,
                -10.0::DOUBLE AS layout_min_x, -10.0::DOUBLE AS layout_min_y,
                10.0::DOUBLE AS layout_max_x, 10.0::DOUBLE AS layout_max_y
            """
        )
        db.execute(
            """
            CREATE TABLE node_metrics_v AS
            SELECT node_id, market_id, 'e'::VARCHAR AS event_key,
                   'component-one'::VARCHAR AS component_id,
                   1::BIGINT AS total_degree, 1::BIGINT AS incoming_degree,
                   1::BIGINT AS outgoing_degree, 0::BIGINT AS complement_degree,
                   0::BIGINT AS equivalent_degree,
                   0::BIGINT AS mutually_exclusive_degree,
                   1::BIGINT AS implies_degree, 0::BIGINT AS compatible_degree,
                   0::BIGINT AS rejected_count, 0::BIGINT AS quarantine_count,
                   'parsed'::VARCHAR AS parse_status,
                   'complete'::VARCHAR AS classification_state,
                   1::BIGINT AS classification_eligible_count,
                   1::BIGINT AS classification_assessed_count,
                   0::BIGINT AS unclassified_pair_count,
                   'complete'::VARCHAR AS classification_status,
                   1.0::DOUBLE AS classification_coverage
            FROM nodes
            """
        )
        db.execute(
            """
            CREATE TABLE visualization_layout_v AS SELECT * FROM (VALUES
                ('component','component-one',NULL::VARCHAR,0.0,0.0,20.0,0::BIGINT,'v','f'),
                ('event','e','component-one',0.0,0.0,10.0,0::BIGINT,'v','f')
            ) t(layout_level,object_id,parent_id,x,y,radius,layout_rank,layout_version,graph_fingerprint)
            """
        )
        for table, filename in (("nodes", "nodes.parquet"), ("edges", "logic_edges.parquet"), ("conditionals", "conditional_edges.parquet"), ("rejected", "rejected_edges.parquet"), ("quarantine", "quarantined_pairs.parquet"), ("candidates", "relation_candidates.parquet")):
            db.execute(f"COPY {table} TO '{q(out / filename)}' (FORMAT PARQUET)")
        for table, filename in (
            ("event_summary_v", "event_summary.parquet"),
            ("event_relation_summary_v", "event_relation_summary.parquet"),
            ("component_summary_v", "component_summary.parquet"),
            ("node_metrics_v", "node_metrics.parquet"),
            ("visualization_layout_v", "visualization_layout.parquet"),
        ):
            db.execute(f"COPY {table} TO '{q(out / filename)}' (FORMAT PARQUET)")
    finally:
        db.close()
    coverage = {
        "schema_version": "coverage-summary-v2",
        "classification_status": "complete",
        "classification_coverage": 1.0,
        "classification_gap": 0.0,
        "all_market_selection": True,
        "markets": 4,
        "propositions": 4,
    }
    (out / "coverage_summary.json").write_text(json.dumps(coverage), encoding="utf-8")
    (out / "viewer_manifest.json").write_text(
        json.dumps({
            "schema_version": VIEWER_ARTIFACT_VERSION,
            "api_version": VIEWER_API_VERSION,
            "graph_content_fingerprint": "fixture",
            "build_mode": "full",
            "validation_status": "EXPERIMENTAL_FULL",
            "input_profile": WC2026_SOURCE_SCHEMA,
            "input": {
                "sha256": "fixture-source",
                "normalized_semantic_fingerprint": "fixture-input-semantics",
            },
            "scope": {
                "source": "oddsfox-pipeline",
                "scope": "wc2026",
                "universe": "knockout_progression",
                "selection": "all_valid_pipeline_wc2026_markets",
                "truncated": False,
            },
            "discovery_semantics_fingerprint": (
                discovery_semantics_fingerprint()
            ),
            "versions": {},
        }),
        encoding="utf-8",
    )
    artifacts = [path.name for path in out.glob("*.parquet")]
    artifacts.extend(
        (
            "oddsfox_graph.duckdb",
            "coverage_summary.json",
            "viewer_manifest.json",
        )
    )
    (out / "build_manifest.json").write_text(
        json.dumps(
            {
                "command": "discover",
                "version": __version__,
                "build_mode": "full",
                "validation_status": "EXPERIMENTAL_FULL",
                "input_hash": "fixture-source",
                "input_schema": WC2026_SOURCE_SCHEMA,
                "input_semantic_fingerprint": "fixture-input-semantics",
                "graph_content_fingerprint": "fixture",
                "scope": {
                    "source": "oddsfox-pipeline",
                    "scope": "wc2026",
                    "universe": "knockout_progression",
                    "selection": "all_valid_pipeline_wc2026_markets",
                    "truncated": False,
                },
                "discovery_semantics_fingerprint": (
                    discovery_semantics_fingerprint()
                ),
                "source_tree_fingerprint": "fixture-source-tree",
                "versions": {
                    "rules": RULE_VERSION,
                    "publication": PUBLICATION_VERSION,
                    "viewer_api": VIEWER_API_VERSION,
                    "viewer_artifacts": VIEWER_ARTIFACT_VERSION,
                },
                "stats": {
                    "build_mode": "full",
                    "validation_status": "EXPERIMENTAL_FULL",
                },
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )
    return out


def _tree_binding(directory: Path) -> dict[str, object]:
    rows = [
        {
            "path": path.relative_to(directory).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]
    return {"sha256": canonical_json_sha256(rows), "file_count": len(rows)}


def _write_model_manifest(path: Path, model_id: str, port: int) -> Path:
    digest = canonical_json_sha256(model_id)
    content = {
        "model_id": model_id,
        "upstream_revision": "fixture",
        "artifact_sha256": digest,
        "artifact_kind": "file",
        "quantization": "Q8_0",
        "license": "Apache-2.0",
        "tokenizer_sha256": digest,
        "chat_template_sha256": digest,
        "runtime": "llama.cpp",
        "runtime_version": "test",
        "loaded_model_identifier": model_id,
        "context_length": 8192,
        "deployment": "local test",
        "inference_origin": f"http://127.0.0.1:{port}/v1",
    }
    path.write_text(
        json.dumps({"manifest_id": canonical_json_sha256(content), **content}),
        encoding="utf-8",
    )
    return path
