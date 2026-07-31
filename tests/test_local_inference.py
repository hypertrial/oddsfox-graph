from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from oddsfox_graph._discovery.cache import CACHE_FILENAME, InferenceCache, cache_entry
from oddsfox_graph._discovery.consensus import merge_parsed_markets, relation_consensus
from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    ParsedMarket,
    ParsedOutcome,
    SourceMarket,
    SourceOutcome,
)
from oddsfox_graph._discovery.inference import (
    AutomationProfile,
    GenerationSettings,
    LocalStructuredClient,
    ModelManifest,
    QualifiedRelation,
    inference_fingerprint,
    manifest_sha256,
    normalize_inference_base_url,
    validate_automation_profile_match,
)
from oddsfox_graph._discovery.provenance import canonical_json_sha256
from oddsfox_graph._discovery.versions import CACHE_ENTRY_VERSION


PRIMARY = "Qwen/Qwen3-4B-GGUF:Q8_0"
VERIFIER = "ibm-granite/granite-3.3-2b-instruct-GGUF:Q8_0"


def _manifest(model: str, port: int = 8080) -> ModelManifest:
    digest = canonical_json_sha256(model)
    content: dict[str, Any] = {
        "model_id": model,
        "upstream_revision": "fixture",
        "artifact_sha256": digest,
        "artifact_kind": "file",
        "quantization": "Q8_0",
        "license": "Apache-2.0",
        "tokenizer_sha256": digest,
        "chat_template_sha256": digest,
        "runtime": "llama.cpp",
        "runtime_version": "b7000",
        "loaded_model_identifier": model,
        "context_length": 8192,
        "deployment": "local fixture",
        "inference_origin": f"http://127.0.0.1:{port}/v1",
    }
    return ModelManifest.model_validate(
        {"manifest_id": canonical_json_sha256(content), **content}
    )


def _settings() -> GenerationSettings:
    return GenerationSettings(0, 0.1, 0.8, 20, 1.5, 1024)


def _assessment(**changes: Any) -> AtomicPairAssessment:
    values: dict[str, Any] = {
        "pair_id": "pair",
        "a_implies_b": "no",
        "b_implies_a": "no",
        "can_both_be_true": "yes",
        "must_one_be_true": "no",
        "logically_related": "yes",
        "confidence": 0.99,
        "supporting_fields": [
            {"proposition": "A", "field": "question", "value": "A?"},
            {"proposition": "B", "field": "question", "value": "B?"},
        ],
        "assumptions": [],
        "unsupported_assumption": False,
        "requires_review": False,
    }
    values.update(changes)
    return AtomicPairAssessment.model_validate(values)


def _proposition(identifier: str, question: str) -> dict[str, object]:
    return {
        "proposition_id": identifier,
        "question": question,
        "description": "",
        "outcome": "Yes",
        "subject": [identifier],
        "predicate": "occur",
        "object": None,
        "operator": None,
        "threshold": None,
        "unit": None,
        "time_start": None,
        "time_end": None,
        "competition": None,
        "event_scope": "scope",
        "jurisdiction": None,
        "polarity": "positive",
    }


def test_url_policy_rejects_secrets_queries_and_remote_without_opt_in() -> None:
    assert normalize_inference_base_url(
        "HTTP://LOCALHOST:8080/v1/", allow_remote=False
    ) == "http://localhost:8080/v1"
    for value in (
        "http://user:secret@localhost:8080/v1",
        "http://localhost:8080/v1?key=secret",
        "http://localhost:8080/v1#fragment",
        "http://localhost:8080/custom",
    ):
        with pytest.raises(ValueError):
            normalize_inference_base_url(value, allow_remote=False)
    with pytest.raises(ValueError, match="Remote inference"):
        normalize_inference_base_url("https://models.example/v1", allow_remote=False)


def test_inference_fingerprint_binds_role_manifest_and_settings() -> None:
    manifest = _manifest(PRIMARY)
    base = dict(
        role="primary_parse",
        requested_model=PRIMARY,
        prompt_version="p1",
        prompt_hash="a" * 64,
        request_schema_hash="b" * 64,
        schema_hash="c" * 64,
        settings=_settings(),
    )
    first = inference_fingerprint(manifest, **base)
    assert first != inference_fingerprint(manifest, **{**base, "role": "verifier_parse"})
    assert first != inference_fingerprint(
        manifest, **{**base, "settings": GenerationSettings(1, 0.1, 0.8, 20, 1.5, 1024)}
    )


def test_relation_consensus_requires_exact_agreement_and_empty_assumptions() -> None:
    a = _proposition("a", "A?")
    b = _proposition("b", "B?")
    compatible = _assessment()
    agreed = relation_consensus(compatible, compatible, a, b, nli_veto=False)
    assert agreed.relation == "compatible"
    assert agreed.status == "agreed"
    disagreement = relation_consensus(
        compatible,
        _assessment(logically_related="no"),
        a,
        b,
        nli_veto=False,
    )
    assert disagreement.status == "model_disagreement"
    assumed = relation_consensus(
        _assessment(assumptions=["same event"]),
        compatible,
        a,
        b,
        nli_veto=False,
    )
    assert assumed.status == "assumption"
    vetoed = relation_consensus(compatible, compatible, a, b, nli_veto=True)
    assert vetoed.status == "nli_veto"


def test_dual_parse_consensus_accepts_normalization_equivalence_and_quarantines_difference() -> None:
    source = SourceMarket(
        market_id="m",
        question="Will BTC win?",
        description="",
        source_hash="a" * 64,
        outcomes=(SourceOutcome(0, "Yes", "yes"), SourceOutcome(1, "No", "no")),
    )
    first = _parsed_market("Bitcoin")
    second = _parsed_market("btc")
    result = merge_parsed_markets(source, first, second)
    assert all(row.status == "agreed" for row in result.values())
    changed = _parsed_market("Ethereum")
    result = merge_parsed_markets(source, first, changed)
    assert all(row.status == "model_disagreement" for row in result.values())


def _parsed_market(subject: str) -> ParsedMarket:
    return ParsedMarket(
        market_id="m",
        propositions=[
            ParsedOutcome(
                outcome=outcome,
                subject=[subject],
                predicate="win",
                object=None,
                operator=None,
                threshold=None,
                unit=None,
                time_start=None,
                time_end=None,
                competition=None,
                event_scope=None,
                jurisdiction=None,
                polarity=polarity,
                parse_confidence=0.99,
                citations=["question", "outcome"],
            )
            for outcome, polarity in (("Yes", "positive"), ("No", "negative"))
        ],
    )


def test_sqlite_cache_role_namespaces_offline_and_transient_replacement(tmp_path: Path) -> None:
    cache = InferenceCache(tmp_path / "cache")
    primary_key = cache.key("primary_parse", "fp", "p", "h", "s", {"id": 1})
    verifier_key = cache.key("verifier_parse", "fp", "p", "h", "s", {"id": 1})
    assert primary_key != verifier_key
    transient = cache_entry(
        task="primary_parse",
        parsed=None,
        error="timeout",
        observed_model=PRIMARY,
        usage={},
        usage_scope=None,
        state="transient_failure",
    )
    cache.put(primary_key, transient)
    assert cache.get(primary_key) is None
    success = cache_entry(
        task="primary_parse",
        parsed={"market_id": "m"},
        error=None,
        observed_model=PRIMARY,
        usage={"total_tokens": 3},
        usage_scope="scope",
        state="success",
    )
    cache.put(primary_key, success)
    cache.close()
    offline = InferenceCache(tmp_path / "cache", offline=True)
    try:
        assert offline.get(primary_key) is not None
        with pytest.raises(ValueError, match="read-only"):
            offline.put(verifier_key, success)
    finally:
        offline.close()


def test_cache_schema_mismatch_and_corruption_fail_loudly(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = InferenceCache(directory)
    cache.close()
    db = sqlite3.connect(directory / CACHE_FILENAME)
    db.execute("UPDATE cache_metadata SET value = '6' WHERE key = 'entry_version'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="empty cache directory"):
        InferenceCache(directory)


def test_cache_version_is_v7() -> None:
    assert CACHE_ENTRY_VERSION == 7
    assert CACHE_FILENAME == "inference-cache-v7.sqlite3"


def test_profile_match_binds_both_models_and_all_fingerprints() -> None:
    primary = _manifest(PRIMARY)
    verifier = _manifest(VERIFIER, 8081)
    fingerprints = {
        "primary_parse": "1",
        "verifier_parse": "2",
        "primary_classify": "3",
        "verifier_classify": "4",
        "nli": "5",
        "consensus": "6",
    }
    content: dict[str, Any] = {
        "status": "AUTOMATION_VALIDATED",
        "case_set_hash": "a" * 64,
        "qualification_generator_version": "catalog-qualification-v1",
        "retrieval_fingerprint": "f" * 64,
        "primary_manifest_id": primary.manifest_id,
        "primary_manifest_sha256": manifest_sha256(primary),
        "verifier_manifest_id": verifier.manifest_id,
        "verifier_manifest_sha256": manifest_sha256(verifier),
        "parse_prompt_hash": "b" * 64,
        "parse_schema_hash": "c" * 64,
        "classify_prompt_hash": "d" * 64,
        "classify_schema_hash": "e" * 64,
        "request_contract_hashes": {"parse": "p", "classify": "c"},
        "inference_fingerprints": fingerprints,
        "relations": {
            relation: QualifiedRelation(enabled=True, threshold=0.98, support=200, precision=1.0)
            for relation in ("complement", "equivalent", "mutually_exclusive", "implies", "compatible")
        },
        "structured_output_validity": {"primary": 1.0, "verifier": 1.0},
        "metrics": {},
    }
    profile = AutomationProfile.model_validate(
        {"profile_id": canonical_json_sha256(content), **content}
    )
    validate_automation_profile_match(
        profile,
        primary,
        verifier,
        fingerprints,
        {"parse": "p", "classify": "c"},
        retrieval_fingerprint="f" * 64,
        parse_prompt_hash="b" * 64,
        parse_schema_hash="c" * 64,
        classify_prompt_hash="d" * 64,
        classify_schema_hash="e" * 64,
    )
    with pytest.raises(ValueError, match="retrieval"):
        validate_automation_profile_match(
            profile,
            primary,
            verifier,
            fingerprints,
            {"parse": "p", "classify": "c"},
            retrieval_fingerprint="0" * 64,
            parse_prompt_hash="b" * 64,
            parse_schema_hash="c" * 64,
            classify_prompt_hash="d" * 64,
            classify_schema_hash="e" * 64,
        )
    with pytest.raises(ValueError, match="verifier"):
        validate_automation_profile_match(
            profile,
            primary,
            _manifest("other", 8081),
            fingerprints,
            {"parse": "p", "classify": "c"},
            retrieval_fingerprint="f" * 64,
            parse_prompt_hash="b" * 64,
            parse_schema_hash="c" * 64,
            classify_prompt_hash="d" * 64,
            classify_schema_hash="e" * 64,
        )


def test_local_chat_completions_contract_and_token_accounting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/props":
            return httpx.Response(200, json={"build_info": "b7000", "n_ctx": 8192})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": PRIMARY}]}, headers={"x-llm-runtime": "llama.cpp", "x-llm-runtime-version": "b7000"})
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        parsed = _assessment().model_dump(mode="json")
        return httpx.Response(200, json={"model": PRIMARY, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(parsed)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    client = LocalStructuredClient("http://127.0.0.1:8080/v1", allow_remote=False)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8080")
    result = asyncio.run(client.generate(model=PRIMARY, system_prompt="/no_think", payload={"pair_id": "pair"}, response_model=AtomicPairAssessment, settings=_settings()))
    asyncio.run(client.aclose())
    assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert requests[-1].url.path == "/v1/chat/completions"


@pytest.mark.parametrize(
    ("runtime", "metadata_path", "metadata", "version"),
    (
        ("llama.cpp", "/props", {"build_info": "b7000", "n_ctx": 8192}, "b7000"),
        ("vllm", "/version", {"version": "0.10.1"}, "0.10.1"),
    ),
)
def test_runtime_preflight_supports_llamacpp_and_vllm(
    runtime: str,
    metadata_path: str,
    metadata: dict[str, object],
    version: str,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": PRIMARY}]},
                headers={"x-llm-runtime": runtime},
            )
        if request.url.path == metadata_path:
            return httpx.Response(200, json=metadata)
        return httpx.Response(404)

    client = LocalStructuredClient(
        "http://127.0.0.1:8080/v1",
        allow_remote=False,
        transport=httpx.MockTransport(handler),
    )
    observed = asyncio.run(
        client.preflight(expected_model=PRIMARY, expected_runtime=runtime)
    )
    asyncio.run(client.aclose())
    assert observed["runtime"] == runtime
    assert observed["runtime_version"] == version
    assert metadata_path in paths
