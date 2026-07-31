from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

from ._discovery.bulk import create_and_fill
from ._discovery.cache import InferenceCache, cache_entry, cache_error
from ._discovery.contracts import (
    DEFAULT_NLI_MODEL,
    DEFAULT_NLI_REVISION,
    AtomicPairAssessment,
    ParsedMarket,
    SourceMarket,
)
from ._discovery.inference import (
    APPROVED_OPEN_LICENSES,
    GenerationSettings,
    LocalStructuredClient,
    ModelManifest,
    ModelProfile,
    inference_fingerprint,
    load_model_manifest,
    manifest_sha256,
    normalize_inference_base_url,
    sha256_path,
)
from ._discovery.protocol import (
    CLASSIFY_PROMPT,
    PARSE_PROMPT,
    classify_request_hash,
    conformance_pair_request,
    conformance_parse_request,
    default_generation_settings,
    market_request,
    model_schema_hash,
    pair_request,
    parse_request_hash,
)
from ._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
    text_sha256,
)
from ._discovery.input import load_source_markets
from ._discovery.nli import (
    ModernBertNliScorer,
    nli_inference_fingerprint,
    score_bidirectional,
)
from ._discovery.adjudication import (
    classification_validation_error,
    derive_atomic_relation,
    nli_text,
)
from ._discovery.parsing import proposition_row, validate_parsed_market
from .queries import DuckDB, q
from ._discovery.versions import (
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_VERSION,
    CLASSIFY_PROMPT_VERSION,
    PARSE_PROMPT_VERSION,
)


def create_model_manifest(
    model_path: Path,
    *,
    model_id: str,
    revision: str,
    license_id: str,
    runtime: str,
    llm_base_url: str,
    output_path: Path,
    allow_remote: bool = False,
) -> dict[str, Any]:
    if license_id not in APPROVED_OPEN_LICENSES:
        raise ValueError(
            f"License {license_id!r} is not approved; expected Apache-2.0"
        )
    origin = normalize_inference_base_url(
        llm_base_url,
        allow_remote=allow_remote,
    )
    artifact_hash, artifact_kind = sha256_path(model_path)
    metadata = asyncio.run(
        _preflight(origin, model_id=model_id, runtime=runtime, allow_remote=allow_remote)
    )
    tokenizer_hash = _metadata_hash(
        model_path,
        ("tokenizer.json", "tokenizer_config.json"),
        fallback=artifact_hash,
    )
    chat_template_hash = _metadata_hash(
        model_path,
        ("chat_template.jinja", "tokenizer_config.json"),
        fallback=artifact_hash,
    )
    quantization = (
        model_id.rsplit(":", 1)[1]
        if ":" in model_id
        else "unquantized-or-runtime-defined"
    )
    runtime_version = str(metadata.get("runtime_version") or "unreported")
    context_length = _context_length(metadata)
    if runtime_version == "unreported" or context_length <= 1:
        raise ValueError(
            "The runtime did not expose complete version and context metadata"
        )
    base: dict[str, Any] = {
        "model_id": model_id,
        "upstream_revision": revision,
        "artifact_sha256": artifact_hash,
        "artifact_kind": artifact_kind,
        "quantization": quantization,
        "license": license_id,
        "tokenizer_sha256": tokenizer_hash,
        "chat_template_sha256": chat_template_hash,
        "runtime": runtime,
        "runtime_version": runtime_version,
        "loaded_model_identifier": model_id,
        "context_length": context_length,
        "deployment": (
            "self-hosted remote endpoint"
            if not _is_loopback(origin)
            else "self-hosted local endpoint"
        ),
        "inference_origin": origin,
    }
    manifest = ModelManifest(
        manifest_id=canonical_json_sha256(base),
        **base,
    )
    atomic_write_json(output_path.resolve(), manifest.model_dump(mode="json"))
    return manifest.model_dump(mode="json")


def check_model(
    model_manifest_path: Path,
    llm_base_url: str,
    *,
    allow_remote: bool = False,
) -> dict[str, Any]:
    manifest = load_model_manifest(model_manifest_path)
    origin = normalize_inference_base_url(
        llm_base_url,
        allow_remote=allow_remote,
    )
    if origin != manifest.inference_origin:
        raise ValueError("model-check endpoint does not match the model manifest")
    report = asyncio.run(_check_model_async(manifest, allow_remote=allow_remote))
    report["metadata_complete"] = (
        report["observed_runtime_version"] not in {None, "", "unreported"}
        and int(report["observed_context_length"]) > 1
    )
    report["passed"] = all(
        (
            report["preflight"],
            report["metadata_complete"],
            report["parse_schema"],
            report["classification_schema"],
            report["token_accounting"],
            report["stable_failure_handling"],
        )
    )
    if not report["passed"]:
        raise RuntimeError("Model runtime conformance failed")
    return report


async def _check_model_async(
    manifest: ModelManifest,
    *,
    allow_remote: bool,
) -> dict[str, Any]:
    client = LocalStructuredClient(
        manifest.inference_origin,
        allow_remote=allow_remote,
    )
    try:
        metadata = await _with_retries(
            lambda: client.preflight(
                expected_model=manifest.loaded_model_identifier,
                expected_runtime=manifest.runtime,
            )
        )
        if metadata.get("runtime_version") and (
            metadata["runtime_version"] != manifest.runtime_version
        ):
            raise ValueError("Runtime version does not match the manifest")
        parse = await client.generate(
            model=manifest.loaded_model_identifier,
            system_prompt=PARSE_PROMPT,
            payload=conformance_parse_request().model_dump(mode="json"),
            response_model=ParsedMarket,
            settings=default_generation_settings(role="parse"),
        )
        classification = await client.generate(
            model=manifest.loaded_model_identifier,
            system_prompt=CLASSIFY_PROMPT,
            payload=conformance_pair_request(
                manifest.loaded_model_identifier
            ).model_dump(mode="json"),
            response_model=AtomicPairAssessment,
            settings=default_generation_settings(role="classify"),
        )
        stable_failure = False
        try:
            await client.generate(
                model=manifest.loaded_model_identifier,
                system_prompt=CLASSIFY_PROMPT,
                payload={"pair_id": "truncation-check"},
                response_model=AtomicPairAssessment,
                settings=GenerationSettings(
                    seed=0,
                    temperature=0.1,
                    top_p=0.8,
                    top_k=20,
                    presence_penalty=1.5,
                    max_output_tokens=1,
                ),
            )
        except Exception as exc:
            stable_failure = getattr(exc, "retryable", True) is False
        parsed_market = ParsedMarket.model_validate(parse.parsed)
        parsed_assessment = AtomicPairAssessment.model_validate(
            classification.parsed
        )
        return {
            "manifest_id": manifest.manifest_id,
            "origin": manifest.inference_origin,
            "runtime": manifest.runtime,
            "runtime_version": manifest.runtime_version,
            "observed_runtime_version": metadata.get("runtime_version"),
            "observed_context_length": _context_length(metadata),
            "preflight": True,
            "parse_schema": (
                parsed_market.market_id == "conformance-market"
                and _contains_each_outcome_once(
                    parsed_market,
                    {"Yes", "No"},
                )
            ),
            "classification_schema": (
                parsed_assessment.pair_id == "conformance-pair"
            ),
            "token_accounting": (
                parse.usage["total_tokens"] > 0
                and classification.usage["total_tokens"] > 0
            ),
            "stable_failure_handling": stable_failure,
            "observed_models": sorted(
                {parse.observed_model, classification.observed_model}
            ),
            "usage": {
                "input_tokens": (
                    parse.usage["input_tokens"]
                    + classification.usage["input_tokens"]
                ),
                "output_tokens": (
                    parse.usage["output_tokens"]
                    + classification.usage["output_tokens"]
                ),
                "total_tokens": (
                    parse.usage["total_tokens"]
                    + classification.usage["total_tokens"]
                ),
            },
        }
    finally:
        await client.aclose()


def build_model_profile(
    input_path: Path,
    benchmark_path: Path,
    cache_dir: Path,
    model_manifest_path: Path,
    out_dir: Path,
    *,
    allow_remote: bool = False,
) -> dict[str, Any]:
    manifest = load_model_manifest(model_manifest_path)
    benchmark_rows = _calibration_rows(benchmark_path)
    if not benchmark_rows:
        raise ValueError("Benchmark contains no calibration partition")
    source_hashes = {
        str(row.get("source_sha256") or "") for row in benchmark_rows
    }
    if source_hashes != {sha256_file(input_path.resolve())}:
        raise ValueError(
            "Calibration benchmark source hash does not match the input parquet"
        )
    if {
        str(row.get("benchmark_version") or "")
        for row in benchmark_rows
    } != {BENCHMARK_VERSION} or {
        str(row.get("schema_version") or "")
        for row in benchmark_rows
    } != {BENCHMARK_SCHEMA_VERSION}:
        raise ValueError("Model profiling requires the current benchmark schema")
    predictions = asyncio.run(
        _calibration_predictions(
            input_path,
            benchmark_rows,
            manifest,
            cache_dir,
            allow_remote=allow_remote,
        )
    )
    profile, report = _profile_from_predictions(
        predictions,
        benchmark_rows,
        benchmark_path,
        manifest,
    )
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_predictions(out_dir / "calibration_predictions.parquet", predictions)
    atomic_write_json(out_dir / "calibration_report.json", report)
    atomic_write_json(out_dir / "model_profile.json", profile.model_dump(mode="json"))
    return report

async def _calibration_predictions(
    input_path: Path,
    benchmark_rows: list[dict[str, Any]],
    manifest: ModelManifest,
    cache_dir: Path,
    *,
    allow_remote: bool,
) -> list[dict[str, Any]]:
    source_schema, _, markets, _ = load_source_markets(
        input_path,
        max_propositions=5_000,
    )
    by_token = {
        outcome.clob_token_id: (market, outcome)
        for market in markets
        for outcome in market.outcomes
    }
    required_token_ids = {
        str(row[field])
        for row in benchmark_rows
        for field in ("proposition_a_id", "proposition_b_id")
        if row.get(field)
    }
    missing_tokens = sorted(required_token_ids - set(by_token))
    if missing_tokens:
        raise ValueError(
            "Calibration benchmark contains proposition IDs outside the "
            f"top-5,000 population: {missing_tokens[:5]}"
        )
    required_market_ids = {
        by_token[token_id][0].market_id for token_id in required_token_ids
    }
    required_markets = {
        market.market_id: market
        for market in markets
        if market.market_id in required_market_ids
    }

    client = LocalStructuredClient(
        manifest.inference_origin,
        allow_remote=allow_remote,
    )
    cache = InferenceCache(cache_dir.resolve())
    semaphore = asyncio.Semaphore(2)
    parse_settings = default_generation_settings(role="parse")
    classify_settings = default_generation_settings(role="classify")
    parse_prompt_hash = text_sha256(PARSE_PROMPT)
    classify_prompt_hash = text_sha256(CLASSIFY_PROMPT)
    parse_schema_hash = model_schema_hash(ParsedMarket)
    classify_schema_hash = model_schema_hash(AtomicPairAssessment)
    parse_fingerprint = inference_fingerprint(
        manifest,
        role="parse",
        requested_model=manifest.loaded_model_identifier,
        prompt_version=PARSE_PROMPT_VERSION,
        prompt_hash=parse_prompt_hash,
        request_schema_hash=parse_request_hash(),
        schema_hash=parse_schema_hash,
        settings=parse_settings,
    )
    classify_fingerprint = inference_fingerprint(
        manifest,
        role="classify",
        requested_model=manifest.loaded_model_identifier,
        prompt_version=CLASSIFY_PROMPT_VERSION,
        prompt_hash=classify_prompt_hash,
        request_schema_hash=classify_request_hash(),
        schema_hash=classify_schema_hash,
        settings=classify_settings,
    )

    def failed_entry(task: str, exc: Exception) -> dict[str, Any]:
        return cache_entry(
            task=task,
            parsed=None,
            error=str(exc),
            observed_model=manifest.loaded_model_identifier,
            usage={},
            usage_scope=None,
            state=(
                "transient_failure"
                if getattr(exc, "retryable", False)
                else "stable_failure"
            ),
            error_type=type(exc).__name__,
            status_code=getattr(exc, "status_code", None),
        )

    try:
        await _with_retries(
            lambda: client.preflight(
                expected_model=manifest.loaded_model_identifier,
                expected_runtime=manifest.runtime,
            )
        )
        parse_requests: dict[
            str, tuple[SourceMarket, str, dict[str, object]]
        ] = {}
        for market_id, market in sorted(required_markets.items()):
            payload = market_request(market).model_dump(mode="json")
            key = cache.key(
                "parse",
                parse_fingerprint,
                PARSE_PROMPT_VERSION,
                parse_prompt_hash,
                parse_schema_hash,
                payload,
            )
            parse_requests[market_id] = (market, key, payload)
        cached_parse = cache.get_many(
            [key for _, key, _ in parse_requests.values()],
            offline=False,
        )

        async def infer_parse(
            market: SourceMarket,
            key: str,
            payload: dict[str, object],
        ) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                try:
                    result = await _with_retries(
                        lambda: client.generate(
                            model=manifest.loaded_model_identifier,
                            system_prompt=PARSE_PROMPT,
                            payload=payload,
                            response_model=ParsedMarket,
                            settings=parse_settings,
                        )
                    )
                    parsed_market = ParsedMarket.model_validate(result.parsed)
                    validate_parsed_market(market, parsed_market)
                    entry = cache_entry(
                        task="parse",
                        parsed=parsed_market.model_dump(mode="json"),
                        error=None,
                        observed_model=result.observed_model,
                        usage=result.usage,
                        usage_scope=cache.usage_scope("parse", [payload]),
                        state="success",
                    )
                except Exception as exc:
                    entry = failed_entry("parse", exc)
                return key, entry

        parse_misses = [
            request
            for request in parse_requests.values()
            if request[1] not in cached_parse
        ]
        parse_results = await asyncio.gather(
            *(infer_parse(*request) for request in parse_misses)
        )
        cache.put_many(dict(sorted(parse_results)))
        parse_entries = {
            market_id: (
                cached_parse.get(key)
                or dict(parse_results).get(key)
            )
            for market_id, (_, key, _) in parse_requests.items()
        }

        propositions: dict[str, dict[str, Any]] = {}
        proposition_errors: dict[str, str] = {}
        for token_id in sorted(required_token_ids):
            market, outcome = by_token[token_id]
            entry = parse_entries.get(market.market_id)
            if entry is None:
                proposition_errors[token_id] = "Missing production parse"
                continue
            error = cache_error(entry)
            if error is not None or entry.get("parsed") is None:
                proposition_errors[token_id] = error or "Missing production parse"
                continue
            try:
                parsed_market = ParsedMarket.model_validate(entry["parsed"])
                validate_parsed_market(market, parsed_market)
                parsed_outcome = next(
                    item
                    for item in parsed_market.propositions
                    if item.outcome == outcome.outcome
                )
                proposition = proposition_row(
                    market,
                    outcome,
                    parsed_outcome,
                    str(
                        entry.get("observed_model")
                        or manifest.loaded_model_identifier
                    ),
                    source_schema,
                    None,
                    parse_fingerprint,
                    None,
                )
                propositions[token_id] = {
                    key: value
                    for key, value in proposition.items()
                    if not key.startswith("_")
                }
            except (StopIteration, TypeError, ValueError) as exc:
                proposition_errors[token_id] = str(exc)

        pair_requests: dict[
            str, tuple[dict[str, object], str, dict[str, Any], dict[str, Any]]
        ] = {}
        for row in benchmark_rows:
            if row["record_type"] != "pair":
                continue
            record_id = str(row["record_id"])
            a_id = str(row["proposition_a_id"])
            b_id = str(row["proposition_b_id"])
            if a_id not in propositions or b_id not in propositions:
                continue
            proposition_a = propositions[a_id]
            proposition_b = propositions[b_id]
            payload = pair_request(
                proposition_a,
                proposition_b,
            ).model_dump(mode="json")
            key = cache.key(
                "classify",
                classify_fingerprint,
                CLASSIFY_PROMPT_VERSION,
                classify_prompt_hash,
                classify_schema_hash,
                payload,
            )
            pair_requests[record_id] = (
                payload,
                key,
                proposition_a,
                proposition_b,
            )
        cached_classify = cache.get_many(
            [key for _, key, _, _ in pair_requests.values()],
            offline=False,
        )
        unique_classify_misses = {
            key: payload
            for payload, key, _, _ in pair_requests.values()
            if key not in cached_classify
        }

        async def infer_classify(
            key: str,
            payload: dict[str, object],
        ) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                try:
                    result = await _with_retries(
                        lambda: client.generate(
                            model=manifest.loaded_model_identifier,
                            system_prompt=CLASSIFY_PROMPT,
                            payload=payload,
                            response_model=AtomicPairAssessment,
                            settings=classify_settings,
                        )
                    )
                    assessment = AtomicPairAssessment.model_validate(
                        result.parsed
                    )
                    if assessment.pair_id != payload["pair_id"]:
                        raise ValueError(
                            "Structured classification returned the wrong pair_id"
                        )
                    entry = cache_entry(
                        task="classify",
                        parsed=assessment.model_dump(mode="json"),
                        error=None,
                        observed_model=result.observed_model,
                        usage=result.usage,
                        usage_scope=cache.usage_scope(
                            "classify",
                            [payload],
                        ),
                        state="success",
                    )
                except Exception as exc:
                    entry = failed_entry("classify", exc)
                return key, entry

        classify_results = await asyncio.gather(
            *(
                infer_classify(key, payload)
                for key, payload in sorted(unique_classify_misses.items())
            )
        )
        cache.put_many(dict(sorted(classify_results)))
        classify_entries = {
            **cached_classify,
            **dict(classify_results),
        }

        predictions: list[dict[str, Any]] = []
        for row in benchmark_rows:
            record_id = str(row["record_id"])
            nli_text_a: str | None = None
            nli_text_b: str | None = None
            try:
                a_id = str(row["proposition_a_id"])
                if a_id in proposition_errors:
                    raise ValueError(proposition_errors[a_id])
                proposition_a = propositions[a_id]
                if row["record_type"] == "parse":
                    predicted = proposition_a
                    confidence = float(proposition_a["parse_confidence"])
                    entry = parse_entries[
                        by_token[a_id][0].market_id
                    ]
                    usage = dict(entry.get("usage") or {}) if entry else {}
                    nli_text_a = None
                    nli_text_b = None
                else:
                    b_id = str(row["proposition_b_id"])
                    if b_id in proposition_errors:
                        raise ValueError(proposition_errors[b_id])
                    payload, key, proposition_a, proposition_b = pair_requests[
                        record_id
                    ]
                    nli_text_a = nli_text(proposition_a)
                    nli_text_b = nli_text(proposition_b)
                    entry = classify_entries.get(key)
                    error_message = cache_error(entry or {})
                    if (
                        entry is None
                        or error_message
                        or entry.get("parsed") is None
                    ):
                        raise ValueError(
                            error_message or "Missing classification"
                        )
                    assessment = AtomicPairAssessment.model_validate(
                        entry["parsed"]
                    )
                    if assessment.pair_id != payload["pair_id"]:
                        raise ValueError(
                            "Structured classification returned the wrong pair_id"
                        )
                    relation, error = derive_atomic_relation(assessment)
                    error = error or classification_validation_error(
                        assessment,
                        proposition_a,
                        proposition_b,
                    )
                    if error is not None:
                        raise ValueError(error)
                    predicted = assessment.model_dump(mode="json")
                    predicted["derived_relation"] = relation
                    confidence = assessment.confidence
                    usage = dict(entry.get("usage") or {})
                predictions.append(
                    {
                        "record_id": record_id,
                        "record_type": str(row["record_type"]),
                        "expected_relation": row.get("expected_relation"),
                        "expected_unsupported_assumption": bool(
                            row.get("unsupported_assumption")
                        ),
                        "predicted_relation": predicted.get(
                            "derived_relation"
                        ),
                        "confidence": float(confidence),
                        "valid": True,
                        "error": None,
                        "prediction_json": json.dumps(
                            predicted,
                            sort_keys=True,
                            default=str,
                        ),
                        "nli_text_a": nli_text_a,
                        "nli_text_b": nli_text_b,
                        "input_tokens": int(usage.get("input_tokens", 0)),
                        "output_tokens": int(usage.get("output_tokens", 0)),
                    }
                )
            except Exception as exc:
                predictions.append(
                    {
                        "record_id": record_id,
                        "record_type": str(row["record_type"]),
                        "expected_relation": row.get("expected_relation"),
                        "expected_unsupported_assumption": bool(
                            row.get("unsupported_assumption")
                        ),
                        "predicted_relation": None,
                        "confidence": 0.0,
                        "valid": False,
                        "error": str(exc),
                        "prediction_json": None,
                        "nli_text_a": nli_text_a,
                        "nli_text_b": nli_text_b,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    }
                )
        return predictions
    finally:
        await client.aclose()
        cache.close()


def _profile_from_predictions(
    predictions: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
    benchmark_path: Path,
    manifest: ModelManifest,
) -> tuple[ModelProfile, dict[str, Any]]:
    pairs = [row for row in predictions if row["record_type"] == "pair"]
    targets = {
        "complement": 0.995,
        "equivalent": 0.99,
        "mutually_exclusive": 0.99,
        "implies": 0.98,
        "compatible": 0.98,
    }
    relation_profiles: dict[str, dict[str, Any]] = {}
    for relation, target in targets.items():
        labels = (
            {"A_implies_B", "B_implies_A"}
            if relation == "implies"
            else {relation}
        )
        relation_profiles[relation] = _calibrate_action(
            [
                (
                    float(row["confidence"]),
                    (
                        str(row["expected_relation"]) in labels
                        and not bool(
                            row.get("expected_unsupported_assumption")
                        )
                    ),
                    str(row["predicted_relation"]) in labels,
                )
                for row in pairs
                if row["valid"]
            ],
            precision_target=target,
        )

    nli_inputs: list[tuple[str, str]] = []
    nli_truth: list[str] = []
    for prediction in pairs:
        if (
            not prediction.get("nli_text_a")
            or not prediction.get("nli_text_b")
        ):
            continue
        nli_inputs.append(
            (
                str(prediction.get("nli_text_a") or ""),
                str(prediction.get("nli_text_b") or ""),
            )
        )
        nli_truth.append(
            (
                str(prediction.get("expected_relation") or "")
                if not prediction.get("expected_unsupported_assumption")
                else "uncertain"
            )
        )
    nli_profiles: dict[str, dict[str, Any]] = {}
    if nli_inputs:
        scorer = ModernBertNliScorer()
        scores = score_bidirectional(scorer, nli_inputs, batch_size=32)
        action_scores = {
            "equivalent": [
                min(item.a_to_b.entailment, item.b_to_a.entailment)
                for item in scores
            ],
            "implies_a_to_b": [
                min(item.a_to_b.entailment, 1.0 - item.b_to_a.entailment)
                for item in scores
            ],
            "implies_b_to_a": [
                min(item.b_to_a.entailment, 1.0 - item.a_to_b.entailment)
                for item in scores
            ],
            "unrelated": [
                min(item.a_to_b.neutral, item.b_to_a.neutral)
                for item in scores
            ],
        }
        expected = {
            "equivalent": "equivalent",
            "implies_a_to_b": "A_implies_B",
            "implies_b_to_a": "B_implies_A",
            "unrelated": "unrelated",
        }
        for action, values in action_scores.items():
            nli_profiles[action] = _calibrate_scores(
                values,
                [truth == expected[action] for truth in nli_truth],
                precision_target=0.99,
            )

    settings_parse = default_generation_settings(role="parse")
    settings_classify = default_generation_settings(role="classify")
    fingerprints = {
        "parse": inference_fingerprint(
            manifest,
            role="parse",
            requested_model=manifest.loaded_model_identifier,
            prompt_version=PARSE_PROMPT_VERSION,
            prompt_hash=text_sha256(PARSE_PROMPT),
            request_schema_hash=parse_request_hash(),
            schema_hash=model_schema_hash(ParsedMarket),
            settings=settings_parse,
        ),
        "classify": inference_fingerprint(
            manifest,
            role="classify",
            requested_model=manifest.loaded_model_identifier,
            prompt_version=CLASSIFY_PROMPT_VERSION,
            prompt_hash=text_sha256(CLASSIFY_PROMPT),
            request_schema_hash=classify_request_hash(),
            schema_hash=model_schema_hash(AtomicPairAssessment),
            settings=settings_classify,
        ),
        "nli": nli_inference_fingerprint(
            DEFAULT_NLI_MODEL,
            DEFAULT_NLI_REVISION,
        ),
    }
    validity = sum(bool(row["valid"]) for row in predictions) / max(
        1,
        len(predictions),
    )
    task_metrics = _calibration_task_metrics(predictions, benchmark_rows)
    calibration_hash = canonical_json_sha256(benchmark_rows)
    base = {
        "model_manifest_id": manifest.manifest_id,
        "model_manifest_sha256": manifest_sha256(manifest),
        "runtime": manifest.runtime,
        "runtime_version": manifest.runtime_version,
        "benchmark_sha256": sha256_file(benchmark_path.resolve()),
        "calibration_partition_sha256": calibration_hash,
        "parse_prompt_hash": text_sha256(PARSE_PROMPT),
        "parse_schema_hash": model_schema_hash(ParsedMarket),
        "classify_prompt_hash": text_sha256(CLASSIFY_PROMPT),
        "classify_schema_hash": model_schema_hash(AtomicPairAssessment),
        "request_contract_hashes": {
            "parse": parse_request_hash(),
            "classify": classify_request_hash(),
        },
        "inference_fingerprints": fingerprints,
        "relations": relation_profiles,
        "nli_actions": nli_profiles,
        "structured_output_validity": validity,
        "metrics": {
            "records": len(predictions),
            "valid_records": sum(bool(row["valid"]) for row in predictions),
            **task_metrics,
        },
    }
    profile = ModelProfile.model_validate(
        {
            **base,
            "profile_id": canonical_json_sha256(base),
        }
    )
    report = {
        "profile_id": profile.profile_id,
        "benchmark_sha256": base["benchmark_sha256"],
        "calibration_partition_sha256": calibration_hash,
        "structured_output_validity": validity,
        "relations": relation_profiles,
        "nli_actions": nli_profiles,
        "enabled_relations": sorted(
            relation
            for relation, value in relation_profiles.items()
            if value["enabled"]
        ),
        "metrics": task_metrics,
        "passed": validity >= 0.999,
    }
    return profile, report


def _calibration_task_metrics(
    predictions: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    truth_by_id = {
        str(row["record_id"]): row for row in benchmark_rows
    }
    parse_fields = {
        "subject": "expected_subjects",
        "predicate": "expected_predicate",
        "object": "expected_object",
        "operator": "expected_operator",
        "threshold": "expected_threshold",
        "unit": "expected_unit",
        "time_start": "expected_time_start",
        "time_end": "expected_time_end",
        "competition": "expected_competition",
        "event_scope": "expected_event_scope",
        "jurisdiction": "expected_jurisdiction",
        "polarity": "expected_polarity",
    }
    field_correct = {field: 0 for field in parse_fields}
    parse_count = 0
    pair_count = 0
    pair_exact = 0
    relation_counts: dict[str, dict[str, int]] = {}
    input_tokens = output_tokens = 0
    for prediction in predictions:
        input_tokens += int(prediction.get("input_tokens") or 0)
        output_tokens += int(prediction.get("output_tokens") or 0)
        truth = truth_by_id[str(prediction["record_id"])]
        if prediction["record_type"] == "parse":
            parse_count += 1
            raw = prediction.get("prediction_json")
            actual = (
                json.loads(str(raw))
                if prediction.get("valid") and raw
                else {}
            )
            for field, expected_field in parse_fields.items():
                expected = truth.get(expected_field)
                observed = actual.get(field)
                if field == "subject":
                    matches = set(observed or []) == set(expected or [])
                else:
                    matches = _calibration_value_equal(observed, expected)
                field_correct[field] += int(matches)
            continue
        pair_count += 1
        expected_relation = str(truth.get("expected_relation") or "")
        predicted_relation = str(
            prediction.get("predicted_relation") or ""
        )
        pair_exact += int(predicted_relation == expected_relation)
        for relation in {expected_relation, predicted_relation} - {""}:
            relation_counts.setdefault(
                relation,
                {"expected": 0, "predicted": 0, "correct": 0},
            )
        relation_counts.setdefault(
            expected_relation,
            {"expected": 0, "predicted": 0, "correct": 0},
        )["expected"] += 1
        if predicted_relation:
            relation_counts[predicted_relation]["predicted"] += 1
            if predicted_relation == expected_relation:
                relation_counts[predicted_relation]["correct"] += 1
    relation_metrics = {
        relation: {
            **counts,
            "precision": (
                counts["correct"] / counts["predicted"]
                if counts["predicted"]
                else None
            ),
            "recall": (
                counts["correct"] / counts["expected"]
                if counts["expected"]
                else None
            ),
        }
        for relation, counts in sorted(relation_counts.items())
        if relation
    }
    return {
        "parser": {
            "records": parse_count,
            "field_accuracy": {
                field: correct / max(1, parse_count)
                for field, correct in field_correct.items()
            },
        },
        "relations": {
            "records": pair_count,
            "exact_accuracy": pair_exact / max(1, pair_count),
            "by_relation": relation_metrics,
        },
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _calibration_value_equal(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return abs(float(first) - float(second)) <= 1e-9
    return str(first) == str(second)


def _contains_each_outcome_once(
    parsed_market: ParsedMarket,
    expected: set[str],
) -> bool:
    observed = [item.outcome for item in parsed_market.propositions]
    return len(observed) == len(expected) and set(observed) == expected


def _calibrate_action(
    rows: list[tuple[float, bool, bool]],
    *,
    precision_target: float,
) -> dict[str, Any]:
    scored = [(score, truth) for score, truth, predicted in rows if predicted]
    return _calibrate_scores(
        [score for score, _ in scored],
        [truth for _, truth in scored],
        precision_target=precision_target,
    )


def _calibrate_scores(
    scores: list[float],
    truth: list[bool],
    *,
    precision_target: float,
) -> dict[str, Any]:
    best: tuple[float, int, float] | None = None
    for threshold in sorted(set(scores), reverse=True):
        selected = [
            expected
            for score, expected in zip(scores, truth, strict=True)
            if score >= threshold
        ]
        if not selected:
            continue
        precision = sum(selected) / len(selected)
        if precision >= precision_target:
            candidate = (threshold, len(selected), precision)
            if best is None or candidate[1] > best[1]:
                best = candidate
    if best is None:
        return {
            "enabled": False,
            "threshold": 1.0,
            "support": 0,
            "precision": 0.0,
        }
    threshold, support, precision = best
    return {
        "enabled": support >= 10,
        "threshold": threshold,
        "support": support,
        "precision": precision,
    }


def _calibration_rows(path: Path) -> list[dict[str, Any]]:
    db = DuckDB()
    try:
        columns = {
            str(row["column_name"])
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(path.resolve())}')"
            )
        }
        if "partition" not in columns:
            raise ValueError(
                "model-profile requires benchmark schema v2 with partitions"
            )
        rows = db.rows(
            f"""
            SELECT *
            FROM read_parquet('{q(path.resolve())}')
            WHERE partition = 'calibration'
            ORDER BY record_type, record_id
            """
        )
        if any(row.get("partition") != "calibration" for row in rows):
            raise RuntimeError("Test-partition leakage detected")
        return cast(list[dict[str, Any]], rows)
    finally:
        db.close()


def _write_predictions(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = {
        "record_id": "VARCHAR",
        "record_type": "VARCHAR",
        "expected_relation": "VARCHAR",
        "expected_unsupported_assumption": "BOOLEAN",
        "predicted_relation": "VARCHAR",
        "confidence": "DOUBLE",
        "valid": "BOOLEAN",
        "error": "VARCHAR",
        "prediction_json": "VARCHAR",
        "nli_text_a": "VARCHAR",
        "nli_text_b": "VARCHAR",
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
    }
    db = DuckDB()
    try:
        create_and_fill(db, "predictions", columns, rows)
        db.execute(
            f"""
            COPY (
                SELECT {", ".join(columns)}
                FROM predictions
                ORDER BY record_type, record_id
            ) TO '{q(path)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()


async def _preflight(
    origin: str,
    *,
    model_id: str,
    runtime: str,
    allow_remote: bool,
) -> dict[str, Any]:
    client = LocalStructuredClient(origin, allow_remote=allow_remote)
    try:
        return cast(
            dict[str, Any],
            await _with_retries(
                lambda: client.preflight(
                    expected_model=model_id,
                    expected_runtime=runtime,
                )
            ),
        )
    finally:
        await client.aclose()


async def _with_retries(call: Any) -> Any:
    for attempt in range(3):
        try:
            return await call()
        except Exception as exc:
            if attempt == 2 or not getattr(exc, "retryable", False):
                raise
            await asyncio.sleep(0.5 * (2**attempt))
    raise RuntimeError("unreachable")


def _metadata_hash(path: Path, names: tuple[str, ...], *, fallback: str) -> str:
    if path.is_file():
        return fallback
    for name in names:
        candidate = path / name
        if candidate.is_file():
            return str(sha256_file(candidate))
    return fallback


def _context_length(health: object) -> int:
    if isinstance(health, dict):
        for key in ("context_length", "n_ctx", "max_model_len"):
            value = health.get(key)
            if isinstance(value, int) and value > 0:
                return value
        for value in health.values():
            nested = _context_length(value)
            if nested > 1:
                return nested
    if isinstance(health, list):
        for value in health:
            nested = _context_length(value)
            if nested > 1:
                return nested
    return 1


def _is_loopback(origin: str) -> bool:
    from urllib.parse import urlsplit

    return (urlsplit(origin).hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
