from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oddsfox_graph import __version__
from oddsfox_graph._discovery.cache import InferenceCache
from oddsfox_graph._discovery.bulk import create_and_fill
from oddsfox_graph._discovery.contracts import (
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_VERIFIER_MODEL,
)
from oddsfox_graph._discovery.inference import load_model_manifest, manifest_sha256
from oddsfox_graph._discovery.provenance import canonical_json_sha256, sha256_file
from oddsfox_graph._discovery.versions import RELEASE_FIXTURE_SCHEMA_VERSION
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
from oddsfox_graph.qualification import (
    QUALIFICATION_CASE_COLUMNS,
    qualification_case_set_hash,
)
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
    assert set(manifest["snapshot_files"]) == {
        "snapshot_nodes.parquet",
        "snapshot_edges.parquet",
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


def test_release_fixture_validates_hashes_profiles_and_baselines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "input.parquet").write_bytes(b"canonical-test-catalog")
    source_hash = sha256_file(root / "input.parquet")
    monkeypatch.setattr(
        release_validation,
        "CANONICAL_CATALOG_SHA256",
        source_hash,
    )
    primary_path = _write_model_manifest(
        root / "primary_model_manifest.json", DEFAULT_PRIMARY_MODEL, 8080
    )
    verifier_path = _write_model_manifest(
        root / "verifier_model_manifest.json", DEFAULT_VERIFIER_MODEL, 8081
    )
    primary = load_model_manifest(primary_path)
    verifier = load_model_manifest(verifier_path)
    case_content = {
        "schema_version": "qualification-cases-v1",
        "generator_version": "catalog-qualification-v1",
        "record_type": "parse",
        "partition": "selection",
        "domain": "sports",
        "expected_relation": None,
        "source_market_ids": ["m1"],
        "source_proposition_ids": ["yes", "no"],
        "generator_id": "fixture",
        "payload_json": "{}",
    }
    case_hash = canonical_json_sha256(case_content)
    case = {**case_content, "case_id": case_hash, "case_hash": case_hash}
    case_set_hash = qualification_case_set_hash([case])
    cases_db = DuckDB()
    try:
        create_and_fill(cases_db, "cases", QUALIFICATION_CASE_COLUMNS, [case])
        cases_db.execute(
            f"COPY cases TO '{q(root / 'qualification_cases.parquet')}' (FORMAT PARQUET)"
        )
    finally:
        cases_db.close()
    profile_content = {
        "status": "AUTOMATION_VALIDATED",
        "case_set_hash": case_set_hash,
        "qualification_generator_version": "catalog-qualification-v1",
        "retrieval_fingerprint": "a" * 64,
        "primary_manifest_id": primary.manifest_id,
        "primary_manifest_sha256": manifest_sha256(primary),
        "verifier_manifest_id": verifier.manifest_id,
        "verifier_manifest_sha256": manifest_sha256(verifier),
        "parse_prompt_hash": "b" * 64,
        "parse_schema_hash": "c" * 64,
        "classify_prompt_hash": "d" * 64,
        "classify_schema_hash": "e" * 64,
        "request_contract_hashes": {"parse": "p", "classify": "c"},
        "inference_fingerprints": {"consensus": "f" * 64},
        "relations": {
            relation: {
                "enabled": True,
                "threshold": 0.99,
                "support": 200,
                "precision": 1.0,
            }
            for relation in (
                "complement",
                "equivalent",
                "mutually_exclusive",
                "implies",
                "compatible",
            )
        },
        "structured_output_validity": {"primary": 1.0, "verifier": 1.0},
        "metrics": {"gates": {"fixture": True}},
    }
    profile_id = canonical_json_sha256(profile_content)
    required = {
        "automation_profile.json": {"profile_id": profile_id, **profile_content},
        "qualification_report.json": {
            "status": "AUTOMATION_VALIDATED",
            "profile_id": profile_id,
            "case_set_hash": case_set_hash,
        },
        "compute_profile.json": {"hardware_hour_usd": 1.0, "currency": "USD"},
        "performance_report.json": {"passed": True},
    }
    for relative, content in required.items():
        (root / relative).write_text(json.dumps(content), encoding="utf-8")
    expected_hashes: dict[str, dict[str, str]] = {}
    for envelope in ("5000", "20000", "all"):
        baseline = root / "baselines" / envelope
        baseline.mkdir(parents=True)
        hashes = {"nodes.parquet": f"hash-{envelope}"}
        expected_hashes[envelope] = hashes
        (baseline / "build_manifest.json").write_text(
            json.dumps(
                {
                    "version": __version__,
                    "inference": {"automation_profile_id": profile_id},
                    "stats": {"qualification_status": "AUTOMATION_VALIDATED"},
                    "artifact_hashes": hashes,
                }
            ),
            encoding="utf-8",
        )
        if envelope == "all":
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
                json.dumps({"graph_content_fingerprint": "fixture"}),
                encoding="utf-8",
            )
    (root / "expected_artifact_hashes.json").write_text(
        json.dumps(expected_hashes), encoding="utf-8"
    )
    cache = InferenceCache(root / "cache")
    cache.put_qualification_profile(
        "release-fixture-profile",
        required["automation_profile.json"],
    )
    cache.close()
    bound = [
        "input.parquet",
        "primary_model_manifest.json",
        "verifier_model_manifest.json",
        *required,
        "qualification_cases.parquet",
        "expected_artifact_hashes.json",
        "baselines/5000/build_manifest.json",
        "baselines/20000/build_manifest.json",
        "baselines/all/build_manifest.json",
        "baselines/all/viewer_manifest.json",
        "baselines/all/coverage_summary.json",
    ]
    fixture = {
        "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
        "package_version": __version__,
        "source_sha256": source_hash,
        "files": {name: sha256_file(root / name) for name in bound},
        "trees": {
            name: _tree_binding(root / name)
            for name in ("cache", "baselines/5000", "baselines/20000", "baselines/all")
        },
    }
    (root / "release-fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    result = validate_release_fixture(root, tmp_path / "work")
    assert result["passed"] is True
    assert result["decision"] == "AUTOMATION_VALIDATED"


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
    report = doctor(
        catalog,
        tmp_path / "out",
        tmp_path / "cache",
        primary,
        verifier,
        "http://127.0.0.1:8080/v1",
        "http://127.0.0.1:8081/v1",
        compute,
    )
    assert report.passed is True
    assert any(check.status == "warn" for check in report.checks)
    compute.write_text("{}", encoding="utf-8")
    failed = doctor(
        catalog,
        tmp_path / "out",
        tmp_path / "cache",
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
                ('a','b','implies','model',0.9,'m1','m2','e','e','a implies b','generative_consensus',NULL,'p','a implies b',[]::VARCHAR[],NULL,'p1','s','c','sc','pm','vm','pf','vf','cf','ap'),
                ('b','c','equivalent','model',0.8,'m2','m3','e','e','b equals c','generative_consensus',NULL,'p','b equals c',[]::VARCHAR[],NULL,'p2','s','c','sc','pm','vm','pf','vf','cf','ap'),
                ('c','d','implies','model',0.95,'m3','m4','e','e','c implies d','generative_consensus',NULL,'p','c implies d',[]::VARCHAR[],NULL,'p3','s','c','sc','pm','vm','pf','vf','cf','ap'),
                ('b','d','implies','model',0.8,'m2','m4','e','e','b implies d','generative_consensus',NULL,'p','b implies d',[]::VARCHAR[],NULL,'p5','s','c','sc','pm','vm','pf','vf','cf','ap'),
                ('a','d','mutually_exclusive','same_market',1.0,'m1','m4','e','e','exclusive','deterministic','v',NULL,'exclusive',[]::VARCHAR[],'rule','p4','s','c','sc',NULL,NULL,NULL,NULL,NULL,NULL)
            ) t(src_node_id,dst_node_id,edge_type,edge_basis,confidence,market_id_src,market_id_dst,event_slug_src,event_slug_dst,evidence,discovery_method,rule_version,prompt_version,explanation,assumptions,rule_id,proposal_id,solver_version,constraint_version,solver_component_id,primary_model_version,verifier_model_version,primary_inference_fingerprint,verifier_inference_fingerprint,consensus_fingerprint,automation_profile_id)
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
        db.execute("CREATE TABLE candidates AS SELECT 'a' AS proposition_a_id, 'c' AS proposition_b_id, ['semantic']::VARCHAR[] AS candidate_reasons")
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
                   'component-fingerprint'::VARCHAR AS component_fingerprint
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
        "classification_coverage": 1.0,
        "classification_gap": 0.0,
        "all_market_selection": True,
        "markets": 4,
        "propositions": 4,
    }
    (out / "coverage_summary.json").write_text(json.dumps(coverage), encoding="utf-8")
    (out / "viewer_manifest.json").write_text(
        json.dumps({"graph_content_fingerprint": "fixture", "versions": {}}),
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
