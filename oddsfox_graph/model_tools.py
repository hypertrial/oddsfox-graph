"""Self-hosted model manifest and runtime-conformance tooling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from ._discovery.contracts import AtomicPairAssessment, ParsedMarket
from ._discovery.inference import (
    APPROVED_OPEN_LICENSES,
    GenerationSettings,
    LocalStructuredClient,
    ModelManifest,
    load_model_manifest,
    normalize_inference_base_url,
    sha256_path,
)
from ._discovery.protocol import (
    CLASSIFY_PROMPT,
    PARSE_PROMPT,
    conformance_pair_request,
    conformance_parse_request,
    default_generation_settings,
)
from ._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
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
    if runtime not in {"llama.cpp", "vllm"}:
        raise ValueError("runtime must be llama.cpp or vllm")
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
    runtime_version = str(metadata.get("runtime_version") or "unreported")
    context_length = _context_length(metadata)
    if runtime_version == "unreported" or context_length <= 1:
        raise ValueError(
            "The runtime did not expose complete version and context metadata"
        )
    content: dict[str, Any] = {
        "model_id": model_id,
        "upstream_revision": revision,
        "artifact_sha256": artifact_hash,
        "artifact_kind": artifact_kind,
        "quantization": (
            model_id.rsplit(":", 1)[1]
            if ":" in model_id
            else "runtime-defined"
        ),
        "license": license_id,
        "tokenizer_sha256": tokenizer_hash,
        "chat_template_sha256": chat_template_hash,
        "runtime": runtime,
        "runtime_version": runtime_version,
        "loaded_model_identifier": model_id,
        "context_length": context_length,
        "deployment": (
            "self-hosted local endpoint"
            if _is_loopback(origin)
            else "self-hosted remote endpoint"
        ),
        "inference_origin": origin,
    }
    manifest = ModelManifest.model_validate(
        {"manifest_id": canonical_json_sha256(content), **content}
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
    report["passed"] = all(
        bool(report[name])
        for name in (
            "preflight",
            "metadata_complete",
            "parse_schema",
            "classification_schema",
            "token_accounting",
            "stable_failure_handling",
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
        if metadata.get("runtime_version") not in {
            None,
            manifest.runtime_version,
        }:
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
        context_length = _context_length(metadata)
        return {
            "manifest_id": manifest.manifest_id,
            "origin": manifest.inference_origin,
            "runtime": manifest.runtime,
            "runtime_version": manifest.runtime_version,
            "observed_runtime_version": metadata.get("runtime_version"),
            "observed_context_length": context_length,
            "preflight": True,
            "metadata_complete": context_length > 1,
            "parse_schema": (
                parsed_market.market_id == "conformance-market"
                and _contains_each_outcome_once(parsed_market, {"Yes", "No"})
            ),
            "classification_schema": (
                parsed_assessment.pair_id == "conformance-pair"
            ),
            "token_accounting": (
                int(parse.usage.get("total_tokens", 0)) > 0
                and int(classification.usage.get("total_tokens", 0)) > 0
            ),
            "stable_failure_handling": stable_failure,
            "observed_models": sorted(
                {parse.observed_model, classification.observed_model}
            ),
            "usage": {
                key: int(parse.usage.get(key, 0))
                + int(classification.usage.get(key, 0))
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
        }
    finally:
        await client.aclose()


async def _preflight(
    origin: str,
    *,
    model_id: str,
    runtime: str,
    allow_remote: bool,
) -> dict[str, Any]:
    client = LocalStructuredClient(origin, allow_remote=allow_remote)
    try:
        return await _with_retries(
            lambda: client.preflight(
                expected_model=model_id,
                expected_runtime=runtime,
            )
        )
    finally:
        await client.aclose()


_Result = TypeVar("_Result")


async def _with_retries(call: Callable[[], Awaitable[_Result]]) -> _Result:
    for attempt in range(3):
        try:
            return await call()
        except Exception as exc:
            if attempt == 2 or not bool(getattr(exc, "retryable", False)):
                raise
            await asyncio.sleep(0.5 * (2**attempt))
    raise AssertionError("retry loop exhausted")


def _metadata_hash(path: Path, names: tuple[str, ...], *, fallback: str) -> str:
    root = path.resolve()
    if root.is_file():
        root = root.parent
    present = [root / name for name in names if (root / name).is_file()]
    if not present:
        return fallback
    return canonical_json_sha256(
        {candidate.name: sha256_file(candidate) for candidate in sorted(present)}
    )


def _context_length(metadata: object) -> int:
    if not isinstance(metadata, dict):
        return 0
    for key in ("context_length", "n_ctx", "max_model_len"):
        value = metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _contains_each_outcome_once(
    parsed: ParsedMarket,
    expected: set[str],
) -> bool:
    outcomes = [row.outcome for row in parsed.propositions]
    return len(outcomes) == len(expected) and set(outcomes) == expected


def _is_loopback(origin: str) -> bool:
    host = urlsplit(origin).hostname
    return host in {"localhost", "127.0.0.1", "::1"}
