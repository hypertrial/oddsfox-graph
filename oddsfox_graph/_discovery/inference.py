from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .provenance import canonical_json_sha256, sha256_file
from .versions import (
    INFERENCE_FINGERPRINT_VERSION,
    MODEL_MANIFEST_SCHEMA_VERSION,
    MODEL_PROFILE_SCHEMA_VERSION,
)


APPROVED_OPEN_LICENSES = frozenset({"Apache-2.0"})


class InferenceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        error_type: str = "inference_error",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.error_type = error_type


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model-manifest-v1"] = MODEL_MANIFEST_SCHEMA_VERSION
    manifest_id: str
    model_id: str
    upstream_revision: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_kind: str
    quantization: str
    license: str
    tokenizer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chat_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: Literal["llama.cpp", "vllm"]
    runtime_version: str
    loaded_model_identifier: str
    context_length: int = Field(gt=0)
    deployment: str
    inference_origin: str


class ProfileRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    threshold: float = Field(ge=0.0, le=1.0)
    support: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)


class NliProfileAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    threshold: float = Field(ge=0.0, le=1.0)
    support: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model-profile-v2"] = MODEL_PROFILE_SCHEMA_VERSION
    profile_id: str
    model_manifest_id: str
    model_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: Literal["llama.cpp", "vllm"]
    runtime_version: str
    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calibration_partition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parse_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parse_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    classify_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    classify_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_contract_hashes: dict[str, str]
    inference_fingerprints: dict[str, str]
    relations: dict[str, ProfileRelation]
    nli_actions: dict[str, NliProfileAction]
    structured_output_validity: float = Field(ge=0.0, le=1.0)
    metrics: dict[str, Any]


class ComputeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hardware_hour_usd: float = Field(ge=0.0)
    load_watts: float | None = Field(default=None, ge=0.0)
    electricity_usd_per_kwh: float | None = Field(default=None, ge=0.0)
    currency: str = "USD"


@dataclass(frozen=True)
class GenerationSettings:
    seed: int
    temperature: float
    top_p: float
    top_k: int
    presence_penalty: float
    max_output_tokens: int


@dataclass(frozen=True)
class StructuredResult:
    parsed: BaseModel
    observed_model: str
    usage: dict[str, int]
    finish_reason: str


class StructuredClient(Protocol):
    async def aclose(self) -> None: ...

    async def preflight(
        self,
        *,
        expected_model: str,
        expected_runtime: str,
    ) -> dict[str, Any]: ...

    async def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        payload: object,
        response_model: type[BaseModel],
        settings: GenerationSettings,
    ) -> StructuredResult: ...


def normalize_inference_base_url(
    value: str,
    *,
    allow_remote: bool,
) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("llm_base_url must use http or https")
    if not parsed.hostname:
        raise ValueError("llm_base_url must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("llm_base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("llm_base_url must not contain a query string or fragment")
    host = parsed.hostname.lower().strip("[]")
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not loopback and not allow_remote:
        raise ValueError(
            "Remote inference requires --allow-remote-inference; "
            "the default policy permits loopback endpoints only"
        )
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ValueError("llm_base_url path must be empty or /v1")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            "",
        )
    )


def sha256_path(path: Path) -> tuple[str, str]:
    resolved = path.resolve()
    if resolved.is_file():
        return sha256_file(resolved), "file"
    if not resolved.is_dir():
        raise ValueError(f"Model path does not exist: {resolved}")
    digest = hashlib.sha256()
    files = sorted(candidate for candidate in resolved.rglob("*") if candidate.is_file())
    if not files:
        raise ValueError(f"Model directory is empty: {resolved}")
    for candidate in files:
        relative = candidate.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(candidate).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), "tree"


def manifest_sha256(manifest: ModelManifest) -> str:
    return canonical_json_sha256(manifest.model_dump(mode="json"))


def inference_fingerprint(
    manifest: ModelManifest,
    *,
    role: str,
    requested_model: str,
    prompt_version: str,
    prompt_hash: str,
    request_schema_hash: str,
    schema_hash: str,
    settings: GenerationSettings,
) -> str:
    return canonical_json_sha256(
        {
            "version": INFERENCE_FINGERPRINT_VERSION,
            "model_manifest_sha256": manifest_sha256(manifest),
            "role": role,
            "requested_model": requested_model,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "request_schema_hash": request_schema_hash,
            "schema_hash": schema_hash,
            "sampling": asdict(settings),
            "runtime": manifest.runtime,
            "runtime_version": manifest.runtime_version,
        }
    )


def load_model_manifest(path: Path) -> ModelManifest:
    try:
        raw = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid model manifest {path}: {exc}") from exc
    manifest = ModelManifest.model_validate(raw)
    manifest_content = manifest.model_dump(mode="json")
    manifest_content.pop("schema_version", None)
    manifest_content.pop("manifest_id", None)
    if manifest.manifest_id != canonical_json_sha256(manifest_content):
        raise ValueError("Model manifest ID does not match its canonical content")
    if manifest.license not in APPROVED_OPEN_LICENSES:
        raise ValueError(
            f"Model license {manifest.license!r} is not approved; "
            f"expected one of {sorted(APPROVED_OPEN_LICENSES)}"
        )
    normalized_origin = normalize_inference_base_url(
        manifest.inference_origin,
        allow_remote=True,
    )
    if manifest.inference_origin != normalized_origin:
        raise ValueError(
            "Model manifest inference origin is not canonical"
        )
    return manifest


def load_model_profile(path: Path) -> ModelProfile:
    try:
        raw = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid model profile {path}: {exc}") from exc
    profile = ModelProfile.model_validate(raw)
    profile_content = profile.model_dump(mode="json")
    profile_content.pop("schema_version", None)
    profile_content.pop("profile_id", None)
    if profile.profile_id != canonical_json_sha256(profile_content):
        raise ValueError("Model profile ID does not match its canonical content")
    return profile


def load_compute_profile(path: Path) -> ComputeProfile:
    try:
        raw = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid compute profile {path}: {exc}") from exc
    return ComputeProfile.model_validate(raw)


def validate_profile_match(
    profile: ModelProfile,
    manifest: ModelManifest,
    fingerprints: dict[str, str],
    request_contract_hashes: dict[str, str],
    *,
    parse_prompt_hash: str,
    parse_schema_hash: str,
    classify_prompt_hash: str,
    classify_schema_hash: str,
) -> None:
    if profile.model_manifest_id != manifest.manifest_id:
        raise ValueError("Model profile does not match the model manifest ID")
    if profile.model_manifest_sha256 != manifest_sha256(manifest):
        raise ValueError("Model profile does not match the model manifest content")
    if profile.runtime != manifest.runtime or (
        profile.runtime_version != manifest.runtime_version
    ):
        raise ValueError("Model profile runtime does not match the model manifest")
    if profile.inference_fingerprints != fingerprints:
        raise ValueError(
            "Model profile inference fingerprints do not match this run"
        )
    if profile.request_contract_hashes != request_contract_hashes:
        raise ValueError(
            "Model profile request contracts do not match this run"
        )
    expected_protocol_hashes = {
        "parse_prompt_hash": parse_prompt_hash,
        "parse_schema_hash": parse_schema_hash,
        "classify_prompt_hash": classify_prompt_hash,
        "classify_schema_hash": classify_schema_hash,
    }
    actual_protocol_hashes = {
        field: str(getattr(profile, field))
        for field in expected_protocol_hashes
    }
    if actual_protocol_hashes != expected_protocol_hashes:
        raise ValueError(
            "Model profile prompt or response schema contracts do not match "
            "this run"
        )


class LocalStructuredClient:
    def __init__(
        self,
        base_url: str,
        *,
        allow_remote: bool,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_inference_base_url(
            base_url,
            allow_remote=allow_remote,
        )
        parsed_origin = urlsplit(self.base_url)
        server_origin = urlunsplit(
            (parsed_origin.scheme, parsed_origin.netloc, "", "", "")
        )
        self._client = httpx.AsyncClient(
            base_url=server_origin,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def preflight(
        self,
        *,
        expected_model: str,
        expected_runtime: str,
    ) -> dict[str, Any]:
        health = await self._request("GET", "/health")
        models = await self._request("GET", "/v1/models")
        runtime_metadata: object = None
        metadata_path = "/props" if expected_runtime == "llama.cpp" else "/version"
        try:
            metadata_response = await self._request("GET", metadata_path)
            runtime_metadata = (
                metadata_response.json()
                if metadata_response.content
                else None
            )
        except (InferenceError, ValueError):
            runtime_metadata = None
        try:
            models_body = models.json()
        except ValueError as exc:
            raise InferenceError(
                "The model endpoint returned invalid JSON from /v1/models",
                retryable=False,
                error_type="invalid_models_response",
            ) from exc
        data = models_body.get("data") if isinstance(models_body, dict) else None
        if not isinstance(data, list):
            raise InferenceError(
                "The model endpoint /v1/models response has no data array",
                retryable=False,
                error_type="invalid_models_response",
            )
        model_ids = [
            str(item.get("id"))
            for item in data
            if isinstance(item, dict) and item.get("id")
        ]
        if len(model_ids) != len(set(model_ids)):
            raise InferenceError(
                "The model endpoint returned duplicate model IDs",
                retryable=False,
                error_type="duplicate_model_ids",
            )
        if expected_model not in model_ids:
            raise InferenceError(
                f"Loaded model IDs {model_ids!r} do not include {expected_model!r}",
                retryable=False,
                error_type="wrong_model_id",
            )
        runtime_header = models.headers.get("x-llm-runtime")
        if runtime_header and runtime_header != expected_runtime:
            raise InferenceError(
                f"Endpoint runtime {runtime_header!r} does not match "
                f"{expected_runtime!r}",
                retryable=False,
                error_type="runtime_mismatch",
            )
        health_payload: object
        if health.content:
            try:
                health_payload = health.json()
            except ValueError:
                health_payload = health.text
        else:
            health_payload = None
        runtime_version = models.headers.get("x-llm-runtime-version")
        if runtime_version is None and isinstance(health_payload, dict):
            runtime_version = next(
                (
                    str(health_payload[key])
                    for key in ("version", "build", "runtime_version")
                    if health_payload.get(key)
                ),
                None,
            )
        if runtime_version is None and isinstance(runtime_metadata, dict):
            runtime_version = next(
                (
                    str(runtime_metadata[key])
                    for key in ("version", "build", "build_info")
                    if runtime_metadata.get(key)
                ),
                None,
            )
        return {
            "origin": self.base_url,
            "health": health_payload,
            "model_ids": model_ids,
            "runtime": runtime_header or expected_runtime,
            "runtime_version": runtime_version,
            "model_metadata": data,
            "runtime_metadata": runtime_metadata,
        }

    async def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        payload: object,
        response_model: type[BaseModel],
        settings: GenerationSettings,
    ) -> StructuredResult:
        schema = response_model.model_json_schema()
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
            "seed": settings.seed,
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "top_k": settings.top_k,
            "presence_penalty": settings.presence_penalty,
            "max_tokens": settings.max_output_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = await self._request(
            "POST",
            "/v1/chat/completions",
            json=body,
        )
        try:
            result = response.json()
        except ValueError as exc:
            raise InferenceError(
                "The inference endpoint returned invalid JSON",
                retryable=False,
                error_type="invalid_json",
            ) from exc
        if not isinstance(result, dict):
            raise InferenceError(
                "The inference endpoint returned a non-object response",
                retryable=False,
                error_type="invalid_response",
            )
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise InferenceError(
                "The inference endpoint must return exactly one choice",
                retryable=False,
                error_type="invalid_choice_count",
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise InferenceError(
                "The inference endpoint returned an invalid choice",
                retryable=False,
                error_type="invalid_choice",
            )
        finish_reason = str(choice.get("finish_reason") or "")
        if finish_reason in {"length", "max_tokens"}:
            raise InferenceError(
                "Structured output was truncated at the output-token limit",
                retryable=False,
                error_type="truncated_output",
            )
        message = choice.get("message")
        refusal = message.get("refusal") if isinstance(message, dict) else None
        if refusal:
            raise InferenceError(
                f"The inference endpoint refused the structured request: {refusal}",
                retryable=False,
                error_type="refusal",
            )
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise InferenceError(
                "The inference endpoint returned empty structured content",
                retryable=False,
                error_type="empty_content",
            )
        try:
            parsed_json = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InferenceError(
                "The structured response content is invalid JSON",
                retryable=False,
                error_type="malformed_structured_output",
            ) from exc
        try:
            parsed = response_model.model_validate(parsed_json)
        except Exception as exc:
            raise InferenceError(
                f"The structured response failed schema validation: {exc}",
                retryable=False,
                error_type="schema_validation",
            ) from exc
        observed_model = str(result.get("model") or "")
        if not observed_model:
            raise InferenceError(
                "The inference response omitted the loaded model identifier",
                retryable=False,
                error_type="missing_model_id",
            )
        if observed_model != model:
            raise InferenceError(
                f"Inference response model {observed_model!r} does not match "
                f"requested model {model!r}",
                retryable=False,
                error_type="wrong_model_id",
            )
        usage_raw = result.get("usage")
        usage = usage_raw if isinstance(usage_raw, dict) else {}
        prompt_tokens = _integer_usage(usage, "prompt_tokens")
        completion_tokens = _integer_usage(usage, "completion_tokens")
        total_tokens = _integer_usage(usage, "total_tokens")
        if total_tokens == 0 and prompt_tokens + completion_tokens > 0:
            total_tokens = prompt_tokens + completion_tokens
        if total_tokens <= 0:
            raise InferenceError(
                "The inference response omitted usable token accounting",
                retryable=False,
                error_type="missing_token_usage",
            )
        return StructuredResult(
            parsed=parsed,
            observed_model=observed_model,
            usage={
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            finish_reason=finish_reason,
        )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise InferenceError(
                f"Could not reach the inference endpoint: {exc}",
                retryable=True,
                error_type="connection_error",
            ) from exc
        if response.is_success:
            return response
        retryable = response.status_code in {408, 409, 429} or (
            response.status_code >= 500
        )
        raise InferenceError(
            f"Inference endpoint returned HTTP {response.status_code}",
            retryable=retryable,
            status_code=response.status_code,
            error_type="http_error",
        )


def _integer_usage(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
