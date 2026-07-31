"""Operational diagnostics and summaries for self-hosted discovery."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ._discovery.cache import InferenceCache
from ._discovery.contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
)
from ._discovery.input import load_source_markets
from ._discovery.inference import (
    load_automation_profile,
    load_compute_profile,
    load_model_manifest,
    validate_automation_profile_match,
    validate_consensus_model_pair,
)
from ._discovery.protocol import (
    CLASSIFY_PROMPT,
    PARSE_PROMPT,
    classify_request_hash,
    consensus_inference_fingerprints,
    model_schema_hash,
    parse_request_hash,
)
from ._discovery.provenance import sha256_file, text_sha256
from ._discovery.versions import CACHE_FILENAME, CANONICAL_CATALOG_SHA256
from .qualification import qualification_retrieval_fingerprint
from .model_tools import check_model


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class DoctorReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    checks: tuple[Check, ...]
    estimates: dict[str, object]


def doctor(
    input_path: Path,
    out_dir: Path,
    cache_dir: Path,
    primary_manifest_path: Path,
    verifier_manifest_path: Path,
    primary_base_url: str,
    verifier_base_url: str,
    compute_profile: Path,
    *,
    allow_remote: bool = False,
) -> DoctorReport:
    checks: list[Check] = []
    estimates: dict[str, object] = {}
    try:
        schema, rows, _, selection = load_source_markets(
            input_path,
            max_propositions=20_000,
        )
        eligible = selection.get("eligible_propositions")
        if not isinstance(eligible, int):
            raise ValueError("Input selection did not report an eligible proposition count")
        propositions = eligible
        estimates.update(
            source_schema=schema,
            market_rows=rows,
            propositions=propositions,
            candidate_upper_bound=min(400_000, propositions * 20),
            selection=selection,
        )
        checks.append(Check(name="input_schema", status="pass", message=schema))
        source_hash = sha256_file(input_path)
        checks.append(
            Check(
                name="canonical_catalog",
                status=(
                    "pass" if source_hash == CANONICAL_CATALOG_SHA256 else "warn"
                ),
                message=(
                    "canonical release catalog"
                    if source_hash == CANONICAL_CATALOG_SHA256
                    else "valid compact catalog, but not the canonical release fixture"
                ),
                details={"sha256": source_hash},
            )
        )
    except Exception as exc:
        checks.append(Check(name="input_schema", status="fail", message=str(exc)))

    for role, path, endpoint in (
        ("primary", primary_manifest_path, primary_base_url),
        ("verifier", verifier_manifest_path, verifier_base_url),
    ):
        try:
            manifest = load_model_manifest(path)
            if manifest.license != "Apache-2.0":
                raise ValueError("Automated qualification requires Apache-2.0")
            result = check_model(path, endpoint, allow_remote=allow_remote)
            checks.append(
                Check(
                    name=f"{role}_runtime",
                    status="pass",
                    message="runtime conformance passed",
                    details={"manifest_id": manifest.manifest_id, "report": result},
                )
            )
        except Exception as exc:
            checks.append(
                Check(name=f"{role}_runtime", status="fail", message=str(exc))
            )

    try:
        validate_consensus_model_pair(
            load_model_manifest(primary_manifest_path),
            load_model_manifest(verifier_manifest_path),
        )
        checks.append(
            Check(
                name="consensus_model_pair",
                status="pass",
                message="official Qwen primary and Granite verifier",
            )
        )
    except Exception as exc:
        checks.append(
            Check(name="consensus_model_pair", status="fail", message=str(exc))
        )

    profile_path = out_dir / "automation_profile.json"
    if profile_path.is_file():
        try:
            profile = load_automation_profile(profile_path)
            primary = load_model_manifest(primary_manifest_path)
            verifier = load_model_manifest(verifier_manifest_path)
            profile_config = DiscoveryConfig(
                cache_dir=cache_dir,
                compute_profile=compute_profile,
                primary_model_manifest=primary_manifest_path,
                verifier_model_manifest=verifier_manifest_path,
                primary_base_url=primary_base_url,
                verifier_base_url=verifier_base_url,
                allow_remote_inference=allow_remote,
                primary_model=primary.loaded_model_identifier,
                verifier_model=verifier.loaded_model_identifier,
            )
            validate_automation_profile_match(
                profile,
                primary,
                verifier,
                consensus_inference_fingerprints(
                    profile_config,
                    primary,
                    verifier,
                ),
                {
                    "parse": parse_request_hash(),
                    "classify": classify_request_hash(),
                },
                retrieval_fingerprint=qualification_retrieval_fingerprint(
                    profile_config
                ),
                parse_prompt_hash=text_sha256(PARSE_PROMPT),
                parse_schema_hash=model_schema_hash(ParsedMarket),
                classify_prompt_hash=text_sha256(CLASSIFY_PROMPT),
                classify_schema_hash=model_schema_hash(AtomicPairAssessment),
            )
            checks.append(
                Check(
                    name="automation_profile",
                    status="pass",
                    message="profile matches the complete inference contract",
                    details={"profile_id": profile.profile_id},
                )
            )
        except Exception as exc:
            checks.append(
                Check(name="automation_profile", status="fail", message=str(exc))
            )
    else:
        checks.append(
            Check(
                name="automation_profile",
                status="warn",
                message="qualification will create a new automation profile",
            )
        )

    cache_database = cache_dir / CACHE_FILENAME
    if cache_database.is_file():
        try:
            cache = InferenceCache(cache_dir, offline=True)
            try:
                integrity = cache.integrity_check()
                checks.append(
                    Check(
                        name="inference_cache",
                        status="pass" if integrity == "ok" else "fail",
                        message=f"SQLite integrity: {integrity}",
                        details={
                            str(key): value for key, value in cache.stats().items()
                        },
                    )
                )
            finally:
                cache.close()
        except Exception as exc:
            checks.append(Check(name="inference_cache", status="fail", message=str(exc)))
    elif not cache_dir.exists() or not any(cache_dir.iterdir()):
        checks.append(
            Check(
                name="inference_cache",
                status="warn",
                message="cache will be created during online discovery",
            )
        )
    else:
        checks.append(
            Check(
                name="inference_cache",
                status="fail",
                message="cache directory is nonempty but has no v0.9 SQLite cache",
            )
        )

    for package, check_name in (
        ("sentence_transformers", "embedding_and_nli"),
        ("pysat", "solver"),
    ):
        available = importlib.util.find_spec(package) is not None
        checks.append(
            Check(
                name=check_name,
                status="pass" if available else "fail",
                message=f"{package} {'available' if available else 'is not installed'}",
            )
        )
    if not compute_profile.is_file():
        checks.append(
            Check(name="compute_profile", status="fail", message="file is missing")
        )
    else:
        try:
            load_compute_profile(compute_profile)
            checks.append(
                Check(name="compute_profile", status="pass", message="valid profile")
            )
        except ValueError as exc:
            checks.append(Check(name="compute_profile", status="fail", message=str(exc)))
    capacity_root = out_dir.parent.resolve()
    while not capacity_root.exists() and capacity_root != capacity_root.parent:
        capacity_root = capacity_root.parent
    usage = shutil.disk_usage(capacity_root)
    estimates["disk_free_bytes"] = usage.free
    checks.append(
        Check(
            name="output_capacity",
            status="pass" if usage.free >= 5 * 1024**3 else "warn",
            message=f"{usage.free} bytes free",
        )
    )
    return DoctorReport(
        passed=not any(check.status == "fail" for check in checks),
        checks=tuple(checks),
        estimates=estimates,
    )


def run_summary(out_dir: Path) -> dict[str, Any]:
    manifest_path = out_dir.resolve() / "build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Discovery output is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "version": manifest.get("version"),
        "input_hash": manifest.get("input_hash"),
        "qualification": manifest.get("qualification"),
        "models": manifest.get("models"),
        "stats": manifest.get("stats"),
        "cache": manifest.get("cache"),
        "compute": manifest.get("compute"),
        "stage_timings": manifest.get("stage_timings"),
    }
