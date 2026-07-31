from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from oddsfox_graph._discovery.cache import (
    CACHE_FILENAME,
    InferenceCache,
    cache_entry,
)
from oddsfox_graph._discovery.candidates import candidate_sort_key
from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
)
from oddsfox_graph._discovery.inference import (
    GenerationSettings,
    InferenceError,
    LocalStructuredClient,
    ModelManifest,
    ModelProfile,
    canonical_json_sha256,
    inference_fingerprint,
    load_model_manifest,
    load_model_profile,
    manifest_sha256,
    normalize_inference_base_url,
    sha256_path,
    validate_profile_match,
)
from oddsfox_graph._discovery.nli import (
    nli_inference_fingerprint,
    profiled_nli_action,
)
from oddsfox_graph._discovery.protocol import (
    PairRequest,
    classify_request_hash,
    conformance_pair_request,
    protocol_metadata,
)
from oddsfox_graph._discovery.adjudication import derive_atomic_relation
from oddsfox_graph.model_tools import (
    _contains_each_outcome_once,
    _profile_from_predictions,
)
from oddsfox_graph.model_tools import create_model_manifest


MODEL_ID = "Qwen/Qwen3-4B-GGUF:Q8_0"


def _manifest() -> ModelManifest:
    digest = canonical_json_sha256("fixture")
    return ModelManifest(
        manifest_id="fixture-manifest",
        model_id=MODEL_ID,
        upstream_revision="fixture-revision",
        artifact_sha256=digest,
        artifact_kind="file",
        quantization="Q8_0",
        license="Apache-2.0",
        tokenizer_sha256=digest,
        chat_template_sha256=digest,
        runtime="llama.cpp",
        runtime_version="b6000",
        loaded_model_identifier=MODEL_ID,
        context_length=8192,
        deployment="local fixture",
        inference_origin="http://127.0.0.1:8080/v1",
    )


def _settings(max_tokens: int = 128) -> GenerationSettings:
    return GenerationSettings(
        seed=0,
        temperature=0.1,
        top_p=0.8,
        top_k=20,
        presence_penalty=1.5,
        max_output_tokens=max_tokens,
    )


def _atomic(**changes: Any) -> AtomicPairAssessment:
    values: dict[str, Any] = {
        "pair_id": "a|b",
        "a_implies_b": "no",
        "b_implies_a": "no",
        "can_both_be_true": "yes",
        "must_one_be_true": "no",
        "logically_related": "no",
        "confidence": 0.99,
        "supporting_fields": [],
        "assumptions": [],
        "unsupported_assumption": False,
        "requires_review": False,
    }
    values.update(changes)
    return AtomicPairAssessment(**values)


def test_url_policy_rejects_secrets_queries_and_implicit_remote() -> None:
    assert normalize_inference_base_url(
        "HTTP://LOCALHOST:8080/v1/",
        allow_remote=False,
    ) == "http://localhost:8080/v1"
    for value in (
        "http://user:secret@localhost:8080/v1",
        "http://localhost:8080/v1?key=secret",
        "http://localhost:8080/v1#fragment",
        "http://localhost:8080/custom/v1",
    ):
        with pytest.raises(ValueError):
            normalize_inference_base_url(value, allow_remote=False)
    with pytest.raises(ValueError, match="Remote inference"):
        normalize_inference_base_url(
            "https://self-hosted.example/v1",
            allow_remote=False,
        )
    assert normalize_inference_base_url(
        "https://self-hosted.example/v1",
        allow_remote=True,
    ) == "https://self-hosted.example/v1"


@pytest.mark.parametrize(
    "changes",
    (
        {"temperature": -0.1},
        {"generation_top_p": 0.0},
        {"generation_top_k": 0},
        {"presence_penalty": 2.1},
        {"sampling_seed": -1},
    ),
)
def test_generation_settings_reject_invalid_values(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        DiscoveryConfig(**changes).validate()


def test_saved_remote_vllm_manifest_preserves_runtime_identity(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    manifest_content = _manifest().model_dump(mode="json")
    manifest_content.pop("schema_version")
    manifest_content.pop("manifest_id")
    manifest_content.update(
        {
            "runtime": "vllm",
            "runtime_version": "0.10.0",
            "deployment": "self-hosted remote endpoint",
            "inference_origin": "https://self-hosted.example/v1",
        }
    )
    manifest = {
        "schema_version": "model-manifest-v1",
        "manifest_id": canonical_json_sha256(manifest_content),
        **manifest_content,
    }
    (out_dir / "model_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    loaded = load_model_manifest(out_dir / "model_manifest.json")

    assert loaded.runtime == "vllm"
    assert (
        loaded.inference_origin
        == "https://self-hosted.example/v1"
    )


def test_parse_conformance_accepts_each_outcome_in_any_order() -> None:
    parsed = ParsedMarket.model_validate(
        {
            "market_id": "conformance-market",
            "propositions": [
                {
                    "outcome": outcome,
                    "subject": ["conformance check"],
                    "predicate": "pass",
                    "object": None,
                    "operator": None,
                    "threshold": None,
                    "unit": None,
                    "time_start": None,
                    "time_end": None,
                    "competition": None,
                    "event_scope": None,
                    "jurisdiction": None,
                    "polarity": polarity,
                    "parse_confidence": 1.0,
                }
                for outcome, polarity in (
                    ("No", "negative"),
                    ("Yes", "positive"),
                )
            ],
        }
    )

    assert _contains_each_outcome_once(parsed, {"Yes", "No"})


def test_llama_cpp_wire_contract_and_token_accounting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/props":
            return httpx.Response(200, json={"build_info": "b6000", "n_ctx": 8192})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": MODEL_ID}]},
                headers={
                    "x-llm-runtime": "llama.cpp",
                    "x-llm-runtime-version": "b6000",
                },
            )
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["seed"] == 0
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": _atomic().model_dump_json(),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )

    async def run() -> None:
        client = LocalStructuredClient(
            "http://127.0.0.1:8080/v1",
            allow_remote=False,
            transport=httpx.MockTransport(handler),
        )
        try:
            metadata = await client.preflight(
                expected_model=MODEL_ID,
                expected_runtime="llama.cpp",
            )
            assert metadata["runtime_version"] == "b6000"
            result = await client.generate(
                model=MODEL_ID,
                system_prompt="/no_think\nJudge every field.",
                payload={"pair_id": "a|b"},
                response_model=AtomicPairAssessment,
                settings=_settings(),
            )
            assert result.usage["total_tokens"] == 30
            assert isinstance(result.parsed, AtomicPairAssessment)
        finally:
            await client.aclose()

    asyncio.run(run())
    assert [request.url.path for request in requests] == [
        "/health",
        "/v1/models",
        "/props",
        "/v1/chat/completions",
    ]


def test_vllm_preflight_and_duplicate_model_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.10.0"})
        return httpx.Response(
            200,
            json={"data": [{"id": MODEL_ID}]},
            headers={"x-llm-runtime": "vllm"},
        )

    async def valid() -> None:
        client = LocalStructuredClient(
            "http://127.0.0.1:8000/v1",
            allow_remote=False,
            transport=httpx.MockTransport(handler),
        )
        try:
            metadata = await client.preflight(
                expected_model=MODEL_ID,
                expected_runtime="vllm",
            )
            assert metadata["runtime_version"] == "0.10.0"
        finally:
            await client.aclose()

    async def duplicate() -> None:
        def duplicate_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200)
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": MODEL_ID}, {"id": MODEL_ID}]},
                )
            return httpx.Response(404)

        client = LocalStructuredClient(
            "http://127.0.0.1:8000/v1",
            allow_remote=False,
            transport=httpx.MockTransport(duplicate_handler),
        )
        try:
            with pytest.raises(InferenceError, match="duplicate"):
                await client.preflight(
                    expected_model=MODEL_ID,
                    expected_runtime="vllm",
                )
        finally:
            await client.aclose()

    asyncio.run(valid())
    asyncio.run(duplicate())


@pytest.mark.parametrize(
    ("status", "retryable"),
    ((400, False), (408, True), (409, True), (429, True), (503, True)),
)
def test_http_failure_classification(status: int, retryable: bool) -> None:
    async def run() -> None:
        client = LocalStructuredClient(
            "http://127.0.0.1:8080/v1",
            allow_remote=False,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, text="failure")
            ),
        )
        try:
            with pytest.raises(InferenceError) as caught:
                await client.generate(
                    model=MODEL_ID,
                    system_prompt="/no_think",
                    payload={"pair_id": "a|b"},
                    response_model=AtomicPairAssessment,
                    settings=_settings(),
                )
            assert caught.value.retryable is retryable
            assert caught.value.status_code == status
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("finish_reason", "content", "model", "error_type"),
    (
        ("length", "{}", MODEL_ID, "truncated_output"),
        ("stop", "", MODEL_ID, "empty_content"),
        ("stop", "{", MODEL_ID, "malformed_structured_output"),
        (
            "stop",
            _atomic().model_dump_json(),
            "wrong-model",
            "wrong_model_id",
        ),
    ),
)
def test_stable_structured_failures(
    finish_reason: str,
    content: str,
    model: str,
    error_type: str,
) -> None:
    response = {
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }

    async def run() -> None:
        client = LocalStructuredClient(
            "http://127.0.0.1:8080/v1",
            allow_remote=False,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=response)
            ),
        )
        try:
            with pytest.raises(InferenceError) as caught:
                await client.generate(
                    model=MODEL_ID,
                    system_prompt="/no_think",
                    payload={"pair_id": "a|b"},
                    response_model=AtomicPairAssessment,
                    settings=_settings(),
                )
            assert caught.value.retryable is False
            assert caught.value.error_type == error_type
        finally:
            await client.aclose()

    asyncio.run(run())


def test_refusal_is_a_stable_failure() -> None:
    async def run() -> None:
        client = LocalStructuredClient(
            "http://127.0.0.1:8080/v1",
            allow_remote=False,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "model": MODEL_ID,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": "",
                                    "refusal": "unsupported request",
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                )
            ),
        )
        try:
            with pytest.raises(InferenceError) as caught:
                await client.generate(
                    model=MODEL_ID,
                    system_prompt="/no_think",
                    payload={"pair_id": "a|b"},
                    response_model=AtomicPairAssessment,
                    settings=_settings(),
                )
            assert caught.value.error_type == "refusal"
            assert caught.value.retryable is False
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("assessment", "relation"),
    (
        (
            _atomic(
                a_implies_b="yes",
                b_implies_a="yes",
                logically_related="yes",
            ),
            "equivalent",
        ),
        (
            _atomic(
                can_both_be_true="no",
                must_one_be_true="yes",
                logically_related="yes",
            ),
            "complement",
        ),
        (
            _atomic(can_both_be_true="no", logically_related="yes"),
            "mutually_exclusive",
        ),
        (
            _atomic(a_implies_b="yes", logically_related="yes"),
            "A_implies_B",
        ),
        (
            _atomic(b_implies_a="yes", logically_related="yes"),
            "B_implies_A",
        ),
        (_atomic(logically_related="yes"), "compatible"),
        (_atomic(), "unrelated"),
        (_atomic(a_implies_b="unknown"), "uncertain"),
    ),
)
def test_every_atomic_relation_mapping(
    assessment: AtomicPairAssessment,
    relation: str,
) -> None:
    assert derive_atomic_relation(assessment) == (relation, None)


def test_atomic_contradiction_cannot_map_to_relation() -> None:
    assessment = _atomic(
        a_implies_b="yes",
        can_both_be_true="no",
        logically_related="yes",
    )
    relation, error = derive_atomic_relation(assessment)
    assert relation is None
    assert "contradicts" in str(error)


def test_manifest_profile_and_fingerprint_bind_every_inference_field() -> None:
    manifest = _manifest()
    first = inference_fingerprint(
        manifest,
        role="classify",
        requested_model=MODEL_ID,
        prompt_version="atomic-v1",
        prompt_hash="a" * 64,
        request_schema_hash="9" * 64,
        schema_hash="b" * 64,
        settings=_settings(),
    )
    changed = inference_fingerprint(
        manifest,
        role="classify",
        requested_model=MODEL_ID,
        prompt_version="atomic-v1",
        prompt_hash="a" * 64,
        request_schema_hash="9" * 64,
        schema_hash="b" * 64,
        settings=_settings(max_tokens=129),
    )
    assert first != changed
    profile = ModelProfile(
        profile_id="profile",
        model_manifest_id=manifest.manifest_id,
        model_manifest_sha256=manifest_sha256(manifest),
        runtime=manifest.runtime,
        runtime_version=manifest.runtime_version,
        benchmark_sha256="c" * 64,
        calibration_partition_sha256="d" * 64,
        parse_prompt_hash="e" * 64,
        parse_schema_hash="f" * 64,
        classify_prompt_hash="1" * 64,
        classify_schema_hash="2" * 64,
        request_contract_hashes={"classify": "9" * 64},
        inference_fingerprints={"classify": first},
        relations={},
        nli_actions={},
        structured_output_validity=1.0,
        metrics={},
    )
    validate_profile_match(
        profile,
        manifest,
        {"classify": first},
        {"classify": "9" * 64},
        parse_prompt_hash="e" * 64,
        parse_schema_hash="f" * 64,
        classify_prompt_hash="1" * 64,
        classify_schema_hash="2" * 64,
    )
    with pytest.raises(ValueError, match="fingerprints"):
        validate_profile_match(
            profile,
            manifest,
            {"classify": changed},
            {"classify": "9" * 64},
            parse_prompt_hash="e" * 64,
            parse_schema_hash="f" * 64,
            classify_prompt_hash="1" * 64,
            classify_schema_hash="2" * 64,
        )
    with pytest.raises(ValueError, match="fingerprints"):
        validate_profile_match(
            profile,
            manifest,
            {
                "classify": first,
                "nli": nli_inference_fingerprint("different-nli", "revision"),
            },
            {"classify": "9" * 64},
            parse_prompt_hash="e" * 64,
            parse_schema_hash="f" * 64,
            classify_prompt_hash="1" * 64,
            classify_schema_hash="2" * 64,
        )
    with pytest.raises(ValueError, match="prompt or response schema"):
        validate_profile_match(
            profile,
            manifest,
            {"classify": first},
            {"classify": "9" * 64},
            parse_prompt_hash="0" * 64,
            parse_schema_hash="f" * 64,
            classify_prompt_hash="1" * 64,
            classify_schema_hash="2" * 64,
        )


def test_pair_request_contract_is_canonical_and_excludes_retrieval_metadata() -> None:
    request = conformance_pair_request(MODEL_ID)
    payload = request.model_dump(mode="json")

    assert set(payload) == {"pair_id", "proposition_A", "proposition_B"}
    assert "candidate_reasons" not in json.dumps(payload)
    assert (
        protocol_metadata()["classify"]["request_schema_hash"]
        == classify_request_hash()
    )

    changed_schema = PairRequest.model_json_schema()
    changed_schema["required"] = [
        field
        for field in changed_schema["required"]
        if field != "pair_id"
    ]
    assert canonical_json_sha256(changed_schema) != classify_request_hash()


def test_nli_signal_prioritizes_otherwise_equal_candidates() -> None:
    baseline = {
        "candidate_reasons": ["embedding"],
        "embedding_similarity": 0.8,
        "proposition_a_id": "a",
        "proposition_b_id": "b",
    }
    strong = {
        **baseline,
        "proposition_b_id": "c",
        "nli_a_to_b_entailment": 0.95,
    }
    assert sorted([baseline, strong], key=candidate_sort_key)[0] is strong


def test_profiled_nli_never_proposes_complement_exclusion_or_compatibility() -> None:
    manifest = _manifest()
    profile = ModelProfile(
        profile_id="profile",
        model_manifest_id=manifest.manifest_id,
        model_manifest_sha256=manifest_sha256(manifest),
        runtime=manifest.runtime,
        runtime_version=manifest.runtime_version,
        benchmark_sha256="c" * 64,
        calibration_partition_sha256="d" * 64,
        parse_prompt_hash="e" * 64,
        parse_schema_hash="f" * 64,
        classify_prompt_hash="1" * 64,
        classify_schema_hash="2" * 64,
        request_contract_hashes={},
        inference_fingerprints={},
        relations={},
        nli_actions={
            "equivalent": {
                "enabled": True,
                "threshold": 0.95,
                "support": 20,
                "precision": 1.0,
            }
        },
        structured_output_validity=1.0,
        metrics={},
    )
    action, relation, _ = profiled_nli_action(
        {
            "nli_a_to_b_entailment": 0.99,
            "nli_b_to_a_entailment": 0.99,
            "nli_a_to_b_neutral": 0.0,
            "nli_b_to_a_neutral": 0.0,
        },
        profile,
    )
    assert (action, relation) == ("equivalent", "equivalent")
    assert relation not in {"complement", "mutually_exclusive", "compatible"}


def test_model_tree_hash_and_incompatible_cache_rejection(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.gguf").write_bytes(b"weights")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    first, kind = sha256_path(model)
    assert kind == "tree"
    (model / "tokenizer.json").write_text('{"changed":true}', encoding="utf-8")
    second, _ = sha256_path(model)
    assert first != second

    legacy = tmp_path / "legacy-cache"
    legacy.mkdir()
    (legacy / "legacy.json").write_text(
        json.dumps({"version": 3, "state": "success"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Incompatible discovery cache"):
        InferenceCache(legacy)


def test_sqlite_cache_bulk_states_offline_and_immutable_success(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "cache"
    cache = InferenceCache(directory)
    success = cache_entry(
        task="classify",
        parsed={"pair_id": "pair"},
        error=None,
        observed_model=MODEL_ID,
        usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        usage_scope="scope",
        state="success",
    )
    transient = cache_entry(
        task="classify",
        parsed=None,
        error="timeout",
        observed_model=MODEL_ID,
        usage={},
        usage_scope=None,
        state="transient_failure",
    )
    cache.put_many({"a": success, "b": transient})
    assert cache.get_many(["a", "b"]) == {"a": success}
    assert cache.get_many(["a", "b"], offline=True) == {
        "a": success,
        "b": transient,
    }
    cache.put("b", success)
    assert cache.get("b") == success
    with pytest.raises(ValueError, match="Conflicting immutable"):
        cache.put(
            "a",
            cache_entry(
                task="classify",
                parsed={"pair_id": "different"},
                error=None,
                observed_model=MODEL_ID,
                usage={},
                usage_scope=None,
                state="success",
            ),
        )
    cache.checkpoint()
    assert cache.integrity_check() == "ok"
    assert cache.stats()["entry_count"] == 2
    cache.close()

    offline = InferenceCache(directory, offline=True)
    assert offline.get("a") == success
    with pytest.raises(ValueError, match="read-only"):
        offline.put("c", success)
    offline.close()
    assert (directory / CACHE_FILENAME).is_file()


def test_sqlite_cache_bulk_chunks_and_concurrent_writers(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "cache"
    InferenceCache(directory).close()
    entry = cache_entry(
        task="parse",
        parsed={"market_id": "fixture", "propositions": []},
        error=None,
        observed_model=MODEL_ID,
        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        usage_scope="scope",
        state="success",
    )

    def write(prefix: str) -> None:
        with InferenceCache(directory) as cache:
            cache.put_many(
                {
                    f"{prefix}-{index:03d}": entry
                    for index in range(300)
                }
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write, prefix)
            for prefix in ("a", "b")
        ]
        for future in futures:
            future.result()

    with InferenceCache(directory) as cache:
        keys = [
            f"{prefix}-{index:03d}"
            for prefix in ("a", "b")
            for index in range(300)
        ]
        assert cache.contains_many(keys) == set(keys)
        stats = cache.stats()
        assert stats["entry_count"] == 600
        assert int(stats["file_bytes"]) > 0
        assert int(stats["storage_bytes"]) >= int(stats["file_bytes"])
        assert len(str(stats["database_hash"])) == 64


def test_sqlite_cache_rejects_schema_mismatch_and_invalid_entry_json(
    tmp_path: Path,
) -> None:
    schema_directory = tmp_path / "schema-cache"
    InferenceCache(schema_directory).close()
    database = schema_directory / CACHE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE cache_metadata SET value = '5' WHERE key = 'entry_version'"
        )
    with pytest.raises(ValueError, match="empty cache directory"):
        InferenceCache(schema_directory)

    columns_directory = tmp_path / "columns-cache"
    InferenceCache(columns_directory).close()
    with sqlite3.connect(columns_directory / CACHE_FILENAME) as connection:
        connection.execute(
            "ALTER TABLE cache_entries RENAME COLUMN task TO invalid_task"
        )
    with pytest.raises(ValueError, match="empty cache directory"):
        InferenceCache(columns_directory)

    mixed_directory = tmp_path / "mixed-cache"
    InferenceCache(mixed_directory).close()
    (mixed_directory / "legacy-entry.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="empty cache directory"):
        InferenceCache(mixed_directory)

    invalid_directory = tmp_path / "invalid-cache"
    cache = InferenceCache(invalid_directory)
    cache.put(
        "invalid",
        cache_entry(
            task="parse",
            parsed={"market_id": "fixture"},
            error=None,
            observed_model=MODEL_ID,
            usage={},
            usage_scope=None,
            state="success",
        ),
    )
    cache.close()
    with sqlite3.connect(invalid_directory / CACHE_FILENAME) as connection:
        connection.execute(
            "UPDATE cache_entries SET parsed_json = '{' "
            "WHERE cache_key = 'invalid'"
        )
    reopened = InferenceCache(invalid_directory)
    try:
        with pytest.raises(json.JSONDecodeError):
            reopened.get("invalid")
    finally:
        reopened.close()


def test_manifest_creation_and_canonical_id_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"local-open-model")

    async def preflight(
        origin: str,
        *,
        model_id: str,
        runtime: str,
        allow_remote: bool,
    ) -> dict[str, Any]:
        assert origin == "http://127.0.0.1:8080/v1"
        assert (model_id, runtime, allow_remote) == (
            MODEL_ID,
            "llama.cpp",
            False,
        )
        return {
            "runtime_version": "b6000",
            "runtime_metadata": {"n_ctx": 8192},
        }

    monkeypatch.setattr("oddsfox_graph.model_tools._preflight", preflight)
    destination = tmp_path / "model-manifest.json"
    created = create_model_manifest(
        model,
        model_id=MODEL_ID,
        revision="upstream-revision",
        license_id="Apache-2.0",
        runtime="llama.cpp",
        llm_base_url="http://127.0.0.1:8080/v1",
        output_path=destination,
    )
    assert load_model_manifest(destination).manifest_id == created["manifest_id"]
    tampered = json.loads(destination.read_text(encoding="utf-8"))
    tampered["runtime_version"] = "tampered"
    destination.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical content"):
        load_model_manifest(destination)


def test_profile_never_calibrates_unsupported_positive_truth(
    tmp_path: Path,
) -> None:
    benchmark = tmp_path / "benchmark.parquet"
    benchmark.write_bytes(b"calibration-fixture")
    predictions = [
        {
            "record_id": f"pair-{index}",
            "record_type": "pair",
            "expected_relation": "equivalent",
            "expected_unsupported_assumption": True,
            "predicted_relation": "equivalent",
            "confidence": 0.999,
            "valid": True,
            "error": None,
            "prediction_json": "{}",
            "nli_text_a": None,
            "nli_text_b": None,
            "input_tokens": 10,
            "output_tokens": 5,
        }
        for index in range(10)
    ]
    truth = [
        {
            "record_id": row["record_id"],
            "record_type": "pair",
            "expected_relation": "equivalent",
            "unsupported_assumption": True,
        }
        for row in predictions
    ]
    profile, report = _profile_from_predictions(
        predictions,
        truth,
        benchmark,
        _manifest(),
    )
    assert not profile.relations["equivalent"].enabled
    assert report["metrics"]["relations"]["exact_accuracy"] == 1.0
    assert report["metrics"]["usage"]["total_tokens"] == 150
    assert "nli" in profile.inference_fingerprints
    profile_path = tmp_path / "model-profile.json"
    profile_path.write_text(profile.model_dump_json(), encoding="utf-8")
    assert load_model_profile(profile_path).profile_id == profile.profile_id


def test_nli_profile_uses_parse_valid_pairs_when_generative_output_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oddsfox_graph._discovery.nli import BidirectionalNliScores, NliScores

    benchmark = tmp_path / "benchmark.parquet"
    benchmark.write_bytes(b"calibration-fixture")
    predictions = [
        {
            "record_id": f"pair-{index}",
            "record_type": "pair",
            "expected_relation": "unrelated",
            "expected_unsupported_assumption": False,
            "predicted_relation": None,
            "confidence": 0.0,
            "valid": False,
            "error": "malformed generative output",
            "prediction_json": None,
            "nli_text_a": f"proposition a {index}",
            "nli_text_b": f"proposition b {index}",
            "input_tokens": 0,
            "output_tokens": 0,
        }
        for index in range(10)
    ]
    truth = [
        {
            "record_id": row["record_id"],
            "record_type": "pair",
            "expected_relation": "unrelated",
            "unsupported_assumption": False,
        }
        for row in predictions
    ]
    observed_pairs: list[tuple[str, str]] = []

    def fake_score_bidirectional(
        _scorer: object,
        pairs: list[tuple[str, str]],
        *,
        batch_size: int,
    ) -> list[BidirectionalNliScores]:
        assert batch_size == 32
        observed_pairs.extend(pairs)
        neutral = NliScores(entailment=0.0, contradiction=0.0, neutral=1.0)
        return [
            BidirectionalNliScores(a_to_b=neutral, b_to_a=neutral)
            for _ in pairs
        ]

    monkeypatch.setattr(
        "oddsfox_graph.model_tools.ModernBertNliScorer",
        lambda: object(),
    )
    monkeypatch.setattr(
        "oddsfox_graph.model_tools.score_bidirectional",
        fake_score_bidirectional,
    )

    profile, _ = _profile_from_predictions(
        predictions,
        truth,
        benchmark,
        _manifest(),
    )

    assert len(observed_pairs) == 10
    assert profile.nli_actions["unrelated"].enabled
    assert profile.nli_actions["unrelated"].support == 10
