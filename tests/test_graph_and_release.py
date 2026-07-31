from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    for envelope in ("5000", "20000"):
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
    ]
    fixture = {
        "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
        "package_version": __version__,
        "source_sha256": source_hash,
        "files": {name: sha256_file(root / name) for name in bound},
        "trees": {
            name: _tree_binding(root / name)
            for name in ("cache", "baselines/5000", "baselines/20000")
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
    db = DuckDB()
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
        for table, filename in (("nodes", "nodes.parquet"), ("edges", "logic_edges.parquet"), ("conditionals", "conditional_edges.parquet"), ("rejected", "rejected_edges.parquet"), ("quarantine", "quarantined_pairs.parquet"), ("candidates", "relation_candidates.parquet")):
            db.execute(f"COPY {table} TO '{q(out / filename)}' (FORMAT PARQUET)")
    finally:
        db.close()
    artifacts = [path.name for path in out.glob("*.parquet")]
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
