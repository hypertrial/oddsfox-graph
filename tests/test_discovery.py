from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
import numpy as np
import pytest

from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
    ParsedOutcome,
)
from oddsfox_graph._discovery.input import load_source_markets
from oddsfox_graph._discovery.versions import SOURCE_SCHEMA
from oddsfox_graph.discovery import DISCOVERY_PARQUET_ARTIFACTS, discover
from oddsfox_graph.graph import Graph
from oddsfox_graph.queries import DuckDB, q


ROOT = Path(__file__).resolve().parents[1]
REAL_INPUT = ROOT / "data" / "polymarket_all_markets_20260730T093857Z.parquet"
REAL_INPUT_SHA256 = "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
requires_real_catalog = pytest.mark.skipif(
    not REAL_INPUT.is_file(), reason="canonical release catalog is external"
)


class _Response:
    def __init__(self, parsed: object, model: str) -> None:
        self.parsed = parsed
        self.observed_model = model
        self.usage = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


class _FakeClient:
    def __init__(self, model: str, *, confidence: float = 0.99) -> None:
        self.model = model
        self.confidence = confidence
        self.closed = False

    async def preflight(self, **_: object) -> dict[str, object]:
        return {"runtime_version": "test-fixture", "context_length": 8192}

    async def aclose(self) -> None:
        self.closed = True

    async def generate(self, **kwargs: object) -> _Response:
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        if kwargs["response_model"] is ParsedMarket:
            parsed = ParsedMarket(
                market_id=str(payload["market_id"]),
                propositions=[
                    ParsedOutcome(
                        outcome=str(outcome["outcome"]),
                        subject=[str(payload["market_id"])],
                        predicate="resolve",
                        object=None,
                        operator=outcome["authoritative_extraction"].get("operator"),
                        threshold=outcome["authoritative_extraction"].get("threshold"),
                        unit=outcome["authoritative_extraction"].get("unit"),
                        time_start=None,
                        time_end=None,
                        competition=None,
                        event_scope=str(payload.get("event_slug") or ""),
                        jurisdiction=None,
                        polarity=(
                            "negative"
                            if str(outcome["outcome"]).casefold() == "no"
                            else "positive"
                        ),
                        parse_confidence=0.99,
                        citations=["question", "outcome"],
                    )
                    for outcome in payload["outcomes"]
                ],
            )
            return _Response(parsed, self.model)
        assert set(payload) == {"pair_id", "proposition_A", "proposition_B"}
        identifier = str(payload["proposition_A"]["proposition_id"])
        relation = next(
            (
                value
                for value in (
                    "complement",
                    "equivalent",
                    "mutually_exclusive",
                    "implies",
                    "unrelated",
                    "uncertain",
                )
                if f"-{value}-" in identifier
            ),
            "compatible",
        )
        judgments = {
            "a_implies_b": "no",
            "b_implies_a": "no",
            "can_both_be_true": "yes",
            "must_one_be_true": "no",
            "logically_related": "yes",
        }
        if relation == "complement":
            judgments.update(can_both_be_true="no", must_one_be_true="yes")
        elif relation == "equivalent":
            judgments.update(a_implies_b="yes", b_implies_a="yes")
        elif relation == "mutually_exclusive":
            judgments.update(can_both_be_true="no")
        elif relation == "implies":
            judgments.update(a_implies_b="yes")
        elif relation == "unrelated":
            judgments.update(logically_related="no")
        elif relation == "uncertain":
            judgments = {key: "unknown" for key in judgments}
        supporting_fields = (
            []
            if relation in {"unrelated", "uncertain"}
            else [
                {
                    "proposition": side,
                    "field": "question",
                    "value": str(payload[f"proposition_{side}"]["question"]),
                }
                for side in ("A", "B")
            ]
        )
        parsed = AtomicPairAssessment(
            pair_id=str(payload["pair_id"]),
            **judgments,
            confidence=self.confidence,
            supporting_fields=supporting_fields,
            assumptions=[],
            unsupported_assumption=False,
            requires_review=relation == "uncertain",
        )
        return _Response(parsed, self.model)


class _AuthoritativeConflictClient(_FakeClient):
    async def generate(self, **kwargs: object) -> _Response:
        response = await super().generate(**kwargs)
        if kwargs["response_model"] is not ParsedMarket:
            return response
        parsed = ParsedMarket.model_validate(response.parsed)
        first = parsed.propositions[0]
        replacement = first.model_copy(
            update={
                "polarity": (
                    "negative" if first.polarity == "positive" else "positive"
                )
            }
        )
        return _Response(
            parsed.model_copy(
                update={"propositions": [replacement, *parsed.propositions[1:]]}
            ),
            self.model,
        )


class _OutageClient(_FakeClient):
    async def generate(self, **kwargs: object) -> _Response:
        if kwargs["response_model"] is ParsedMarket:
            raise ConnectionError("endpoint unavailable")
        return await super().generate(**kwargs)


def _embeddings(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
    vectors = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        vector = np.asarray(
            [int.from_bytes(digest[index : index + 4], "big") for index in (0, 4, 8, 12)],
            dtype=np.float32,
        )
        vector /= np.linalg.norm(vector)
        vectors.append(vector)
    return np.asarray(vectors)


def _write_catalog(path: Path) -> None:
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE catalog AS SELECT * FROM (VALUES
                ('e1','event-one','m1','Will Alpha happen?','Alpha.', ['Yes','No']::VARCHAR[], ['m1-y','m1-n']::VARCHAR[],300.0,'2026-01-01'::TIMESTAMP,'2026-12-31'::TIMESTAMP,'events',['alpha']::VARCHAR[]),
                ('e2','event-two','m2','Will Beta happen?','Beta.', ['Yes','No']::VARCHAR[], ['m2-y','m2-n']::VARCHAR[],200.0,'2026-01-01'::TIMESTAMP,'2026-12-31'::TIMESTAMP,'events',['beta']::VARCHAR[]),
                ('e3','event-three','m3','Which color wins?','One color.', ['Red','Blue','Green']::VARCHAR[], ['m3-r','m3-b','m3-g']::VARCHAR[],100.0,'2026-01-01'::TIMESTAMP,'2026-12-31'::TIMESTAMP,'events',['color']::VARCHAR[])
            ) t(event_id,event_slug,market_id,question,description,outcomes,clob_token_ids,volume,start_time,end_time,category,tags)
            """
        )
        db.execute(f"COPY catalog TO '{q(path)}' (FORMAT PARQUET)")
    finally:
        db.close()


@requires_real_catalog
def test_canonical_catalog_binding() -> None:
    assert hashlib.sha256(REAL_INPUT.read_bytes()).hexdigest() == REAL_INPUT_SHA256
    db = DuckDB()
    try:
        assert db.scalar(f"SELECT count(*) FROM read_parquet('{q(REAL_INPUT)}')") == 94_781
    finally:
        db.close()


def test_compact_loader_is_deterministic_and_rejects_old_exports(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    schema, rows, markets, selection = load_source_markets(catalog, max_propositions=5)
    assert schema == SOURCE_SCHEMA
    assert rows == 3
    assert [market.market_id for market in markets] == ["m1", "m2"]
    assert selection["selected_propositions"] == 4
    old = tmp_path / "old.parquet"
    db = DuckDB()
    try:
        db.execute(f"COPY (SELECT 'm' market_id, 'q' question, 'Yes' outcome_label, 't' clob_token_id) TO '{q(old)}' (FORMAT PARQUET)")
    finally:
        db.close()
    with pytest.raises(ValueError, match=SOURCE_SCHEMA):
        load_source_markets(old)


def test_any_model_authoritative_conflict_quarantines_only_affected_outcome(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        max_propositions=2,
        max_candidates=100,
        max_llm_pairs=2,
        top_k=1,
        progress_format="quiet",
    )
    discover(
        catalog,
        tmp_path / "out",
        config=config,
        _primary_client=_AuthoritativeConflictClient(config.primary_model),
        _verifier_client=_FakeClient(config.verifier_model),
        _embedder=_embeddings,
    )
    db = DuckDB()
    try:
        propositions = db.rows(
            f"SELECT proposition_id, parse_status FROM read_parquet('{q(tmp_path / 'out' / 'propositions.parquet')}') ORDER BY proposition_id"
        )
        assessments = db.rows(
            f"SELECT model_role, status, authoritative_conflicts FROM read_parquet('{q(tmp_path / 'out' / 'parse_assessments.parquet')}') WHERE proposition_id = 'm1-y' ORDER BY model_role"
        )
    finally:
        db.close()
    assert {row["proposition_id"]: row["parse_status"] for row in propositions} == {
        "m1-n": "parsed",
        "m1-y": "quarantined",
    }
    assert assessments[0]["model_role"] == "primary"
    assert assessments[0]["status"] == "invalid"
    assert assessments[0]["authoritative_conflicts"]
    assert assessments[1]["status"] == "valid"
    assert assessments[1]["authoritative_conflicts"] == []


def test_parse_quarantine_blocks_model_and_parse_derived_relations(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        max_propositions=4,
        max_candidates=100,
        max_llm_pairs=10,
        top_k=2,
        progress_format="quiet",
    )
    discover(
        catalog,
        tmp_path / "out",
        config=config,
        _primary_client=_AuthoritativeConflictClient(config.primary_model),
        _verifier_client=_FakeClient(config.verifier_model),
        _embedder=_embeddings,
    )
    db = DuckDB()
    try:
        model_edges = db.scalar(
            f"SELECT count(*) FROM read_parquet("
            f"'{q(tmp_path / 'out' / 'logic_edges.parquet')}') "
            "WHERE discovery_method = 'generative_consensus' "
            "AND (src_node_id IN ('m1-y', 'm2-y') "
            "OR dst_node_id IN ('m1-y', 'm2-y'))"
        )
        unsafe_candidates = db.scalar(
            f"SELECT count(*) FROM read_parquet("
            f"'{q(tmp_path / 'out' / 'relation_candidates.parquet')}') "
            "WHERE deterministic_relation IS NULL "
            "AND (proposition_a_id IN ('m1-y', 'm2-y') "
            "OR proposition_b_id IN ('m1-y', 'm2-y')) "
            "AND status != 'quarantined_parse'"
        )
    finally:
        db.close()
    assert model_edges == 0
    assert unsafe_candidates == 0


def test_published_consensus_edge_uses_lower_model_confidence(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        max_propositions=4,
        max_candidates=100,
        max_llm_pairs=10,
        top_k=2,
        progress_format="quiet",
    )
    discover(
        catalog,
        tmp_path / "out",
        config=config,
        _primary_client=_FakeClient(config.primary_model, confidence=0.99),
        _verifier_client=_FakeClient(config.verifier_model, confidence=0.985),
        _embedder=_embeddings,
    )
    db = DuckDB()
    try:
        confidences = db.rows(
            f"SELECT DISTINCT confidence, primary_assessment_id, "
            f"verifier_assessment_id FROM read_parquet("
            f"'{q(tmp_path / 'out' / 'logic_edges.parquet')}') "
            "WHERE discovery_method = 'generative_consensus'"
        )
        assessment_ids = {
            str(row["assessment_id"])
            for row in db.rows(
                f"SELECT assessment_id FROM read_parquet("
                f"'{q(tmp_path / 'out' / 'model_assessments.parquet')}') "
                "WHERE status = 'valid'"
            )
        }
    finally:
        db.close()
    assert {row["confidence"] for row in confidences} == {0.985}
    assert all(
        row["primary_assessment_id"] in assessment_ids
        and row["verifier_assessment_id"] in assessment_ids
        for row in confidences
    )


def test_aggregate_endpoint_loss_aborts_and_transient_cache_recovers(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        max_propositions=2,
        max_candidates=100,
        max_llm_pairs=2,
        top_k=1,
        progress_format="quiet",
    )
    with pytest.raises(RuntimeError, match="primary endpoint"):
        discover(
            catalog,
            tmp_path / "out",
            config=config,
            _primary_client=_OutageClient(config.primary_model),
            _verifier_client=_FakeClient(config.verifier_model),
            _embedder=_embeddings,
        )
    assert not (tmp_path / "out" / "build_manifest.json").exists()
    discover(
        catalog,
        tmp_path / "out",
        config=config,
        _primary_client=_FakeClient(config.primary_model),
        _verifier_client=_FakeClient(config.verifier_model),
        _embedder=_embeddings,
    )
    assert (tmp_path / "out" / "build_manifest.json").is_file()


@requires_real_catalog
def test_dual_model_online_offline_incremental_and_graph_queries(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    config = DiscoveryConfig(
        cache_dir=cache,
        top_k=20,
        max_propositions=6,
        max_candidates=400_000,
        max_llm_pairs=20,
        progress_format="quiet",
    )
    primary = _FakeClient(config.primary_model)
    verifier = _FakeClient(config.verifier_model)
    first = discover(
        REAL_INPUT,
        out,
        config=config,
        _primary_client=primary,
        _verifier_client=verifier,
        _embedder=_embeddings,
    )
    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    first_hashes = manifest["artifact_hashes"]
    assert manifest["version"] == "0.9.0"
    assert manifest["stats"]["qualification_status"] == "AUTOMATION_VALIDATED"
    assert manifest["inference"]["primary"]["manifest_id"]
    assert manifest["inference"]["verifier"]["manifest_id"]
    assert set(DISCOVERY_PARQUET_ARTIFACTS) <= {path.name for path in out.glob("*.parquet")}
    assert not (out / "review_queue.parquet").exists()
    assert not (out / "benchmark.parquet").exists()
    assert first["tokens"] == 6

    offline = discover(
        REAL_INPUT,
        out,
        config=DiscoveryConfig(**{**config.__dict__, "offline": True}),
        _embedder=_embeddings,
    )
    replay_manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert replay_manifest["artifact_hashes"] == first_hashes
    assert offline["incremental"]["offline_state_replay"] is True

    incremental_out = tmp_path / "incremental"
    incremental = discover(
        REAL_INPUT,
        incremental_out,
        config=DiscoveryConfig(
            **{
                **config.__dict__,
                "offline": True,
                "incremental_from": out,
                "primary_model_manifest": out / "primary_model_manifest.json",
                "verifier_model_manifest": out / "verifier_model_manifest.json",
            }
        ),
        _embedder=_embeddings,
    )
    incremental_manifest = json.loads(
        (incremental_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert incremental_manifest["artifact_hashes"] == first_hashes
    assert incremental["incremental"]["candidate_generation_reused"] is True

    graph = Graph.open(out)
    assert len(graph.nodes()) == 6
    assert graph.edges("complement")
    methods = {edge.discovery_method for edge in graph.edges(top=100)}
    assert methods <= {"deterministic", "generative_consensus"}


def test_incompatible_v08_incremental_baseline_is_rejected(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "build_manifest.json").write_text(
        json.dumps({"command": "discover", "version": "0.8.0", "versions": {"candidate_state": "candidate-components-v5"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incompatible"):
        discover(
            catalog,
            tmp_path / "out",
            config=DiscoveryConfig(incremental_from=baseline, max_propositions=4),
            _primary_client=_FakeClient(DiscoveryConfig().primary_model),
            _verifier_client=_FakeClient(DiscoveryConfig().verifier_model),
            _embedder=_embeddings,
        )


def test_incremental_baseline_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        max_propositions=4,
        max_candidates=100,
        max_llm_pairs=10,
        top_k=2,
        progress_format="quiet",
    )
    discover(
        catalog,
        baseline,
        config=config,
        _primary_client=_FakeClient(config.primary_model),
        _verifier_client=_FakeClient(config.verifier_model),
        _embedder=_embeddings,
    )

    for index, relative_path in enumerate(
        (Path("state/proposition_embeddings.parquet"), Path("relation_candidates.parquet"))
    ):
        tampered = tmp_path / f"tampered-{index}"
        shutil.copytree(baseline, tampered)
        (tampered / relative_path).write_bytes(b"tampered")
        with pytest.raises(ValueError, match="incompatible"):
            discover(
                catalog,
                tmp_path / f"incremental-{index}",
                config=DiscoveryConfig(
                    **{
                        **config.__dict__,
                        "incremental_from": tampered,
                    }
                ),
                _primary_client=_FakeClient(config.primary_model),
                _verifier_client=_FakeClient(config.verifier_model),
                _embedder=_embeddings,
            )
