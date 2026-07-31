from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from . import publication as discovery_publication
from .bulk import create_and_fill as _create_and_fill
from .cache import (
    InferenceCache,
    cache_entry,
    cache_error,
)
from .candidates import (
    candidate_sort_key as _candidate_sort_key,
    structural_member_limit as _structural_member_limit,
)
from .contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
    ParsedOutcome,
    SourceMarket,
    SourceOutcome,
)
from .consensus import (
    MODEL_ASSESSMENT_COLUMNS,
    PARSE_ASSESSMENT_COLUMNS,
    QUARANTINE_COLUMNS,
    merge_parsed_markets,
    model_assessment_row,
    nli_contradicts,
    parse_assessment_row,
    quarantine_row,
    relation_consensus,
)
from .inference import (
    AutomationProfile,
    LocalStructuredClient,
    ModelManifest,
    StructuredClient,
    inference_fingerprint,
    load_automation_profile,
    load_compute_profile,
    load_model_manifest,
    manifest_sha256,
    normalize_inference_base_url,
    validate_consensus_model_pair,
    validate_automation_profile_match,
)
from .protocol import (
    CLASSIFY_PROMPT as _CLASSIFY_PROMPT,
    PARSE_PROMPT as _PARSE_PROMPT,
    classify_request_hash,
    consensus_inference_fingerprints,
    deterministic_extract,
    generation_settings as _generation_settings,
    market_request,
    model_schema_hash as _model_schema_hash,
    pair_identifier as _pair_id,
    pair_request,
    parse_request_hash,
    public_proposition as _public_proposition,
)
from .provenance import (
    atomic_write_json as _write_json_atomic,
    canonical_json_sha256,
    compute_accounting,
    peak_rss_mb as _peak_rss_mb,
    sha256_file as _sha256,
    text_sha256 as _text_hash,
)
from .input import (
    load_source_markets as _load_source_markets,
    str_or_none as _str_or_none,
)
from .relation_logic import (
    classification_validation_error as _classification_validation_error,
    derive_atomic_relation as _derive_atomic_relation,
    nli_text as _nli_text,
)
from .parsing import (
    canonical_entity,
    canonical_unit,
    normalize_optional,
    proposition_row as _proposition_row,
    validate_parsed_market as _validate_parsed_market,
)
from .retrieval import generate_candidate_workspace
from .metrics import RunState, StageRecorder
from .nli import (
    ModernBertNliScorer,
    NliScorer,
    score_bidirectional,
    scores_to_columns,
)
from .incremental import EXECUTION_PLAN_COLUMNS, ExecutionPlan
from .publication import (
    copy_sorted_parquet as _copy_table,
    publish_directory_atomically,
    write_conditionals,
)
from .types import (
    ConsensusStageResult,
    IncrementalPreparation,
    IncrementalResources,
    IncrementalStats,
    ParsingStageResult,
    PublicationStageResult,
    RetrievalStageResult,
    SolvingStageResult,
)
from .relations import (
    RULE_REGISTRY,
    deterministic_relation as _deterministic_relation,
    is_winner_proposition as _is_winner_proposition,
    stage_rank as _stage_rank,
)
from .solver import (
    proposal_set_hash,
    solve_proposals,
)
from .versions import (
    AUTOMATION_PROFILE_SCHEMA_VERSION,
    CACHE_ENTRY_VERSION,
    CANONICAL_CATALOG_SHA256,
    CANDIDATE_STATE_VERSION,
    CLASSIFY_PROMPT_VERSION,
    CONSTRAINT_VERSION,
    DOMAIN_TAXONOMY_VERSION,
    EXECUTION_PLAN_VERSION,
    NLI_INFERENCE_VERSION,
    NORMALIZATION_VERSION,
    PARSE_PROMPT_VERSION,
    PUBLICATION_VERSION,
    RETRIEVAL_VERSION,
    RULE_VERSION,
    SOLVER_VERSION,
    QUALIFICATION_GENERATOR_VERSION,
)
from ..qualification import (
    QUALIFICATION_CASE_COLUMNS,
    QualificationPrediction,
    evaluate_qualification,
    generate_qualification_cases,
    qualify_rule_registry,
    qualification_retrieval_fingerprint,
    qualification_retrieved_case_ids,
    qualification_case_set_hash,
)
from .workspace import (
    CANDIDATE_BLOCK_COLUMNS,
    CANDIDATE_COLUMNS,
    CANDIDATE_REASON_COLUMNS,
    EMBEDDING_STATE_COLUMNS,
    SEMANTIC_NEIGHBOR_STATE_COLUMNS,
    CandidateStore,
)
from .. import __version__
from ..artifacts import ARTIFACT_COLUMNS, reports
from ..graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT, write_graph_snapshot
from ..queries import DuckDB, q
from ..reports import write_reports, write_summary_report


DISCOVERY_PARQUET_ARTIFACTS = (
    "nodes.parquet",
    "market_groups.parquet",
    "propositions.parquet",
    "relation_candidates.parquet",
    "logic_edges.parquet",
    "conditional_edges.parquet",
    "parse_assessments.parquet",
    "model_assessments.parquet",
    "quarantined_pairs.parquet",
    "qualification_cases.parquet",
    "rejected_edges.parquet",
    "parse_errors.parquet",
)
GRAPH_DATABASE_ARTIFACT = "oddsfox_graph.duckdb"
_ACTIVE_PROGRESS: ContextVar[StageRecorder | None] = ContextVar(
    "oddsfox_graph_progress",
    default=None,
)
STATE_ARTIFACTS = (
    "state/market_state.parquet",
    "state/proposition_fingerprints.parquet",
    "state/proposition_embeddings.parquet",
    "state/semantic_neighbors.parquet",
    "state/candidate_components.parquet",
    "state/candidate_blocks.parquet",
    "state/candidate_reason_rows.parquet",
    "state/solver_components.parquet",
    "state/execution_plan.parquet",
)

PROPOSITION_COLUMNS = {
    "proposition_id": "VARCHAR",
    "market_id": "VARCHAR",
    "event_id": "VARCHAR",
    "event_slug": "VARCHAR",
    "clob_token_id": "VARCHAR",
    "outcome_index": "INTEGER",
    "outcome": "VARCHAR",
    "question": "VARCHAR",
    "description": "VARCHAR",
    "market_source_hash": "VARCHAR",
    "normalization_version": "VARCHAR",
    "category": "VARCHAR",
    "tags": "VARCHAR[]",
    "subject_original": "VARCHAR[]",
    "subject": "VARCHAR[]",
    "predicate": "VARCHAR",
    "object_original": "VARCHAR",
    "object": "VARCHAR",
    "operator": "VARCHAR",
    "threshold": "DOUBLE",
    "unit_original": "VARCHAR",
    "unit": "VARCHAR",
    "time_start": "TIMESTAMPTZ",
    "time_end": "TIMESTAMPTZ",
    "competition_original": "VARCHAR",
    "competition": "VARCHAR",
    "event_scope_original": "VARCHAR",
    "event_scope": "VARCHAR",
    "jurisdiction_original": "VARCHAR",
    "jurisdiction": "VARCHAR",
    "polarity": "VARCHAR",
    "parse_confidence": "DOUBLE",
    "parse_status": "VARCHAR",
    "primary_parser_model": "VARCHAR",
    "verifier_parser_model": "VARCHAR",
    "prompt_version": "VARCHAR",
    "primary_parse_fingerprint": "VARCHAR",
    "verifier_parse_fingerprint": "VARCHAR",
    "consensus_fingerprint": "VARCHAR",
    "automation_profile_id": "VARCHAR",
    "source_schema": "VARCHAR",
}

REJECTED_EDGE_COLUMNS = {
    "proposal_id": "VARCHAR",
    "src_node_id": "VARCHAR",
    "dst_node_id": "VARCHAR",
    "edge_type": "VARCHAR",
    "edge_basis": "VARCHAR",
    "confidence": "DOUBLE",
    "discovery_method": "VARCHAR",
    "rule_id": "VARCHAR",
    "rule_version": "VARCHAR",
    "prompt_version": "VARCHAR",
    "rejection_reason": "VARCHAR",
    "conflicting_proposal_ids": "VARCHAR[]",
    "conflicting_constraint_ids": "VARCHAR[]",
    "solver_component_id": "VARCHAR",
    "primary_model_version": "VARCHAR",
    "verifier_model_version": "VARCHAR",
    "primary_assessment_id": "VARCHAR",
    "verifier_assessment_id": "VARCHAR",
    "primary_inference_fingerprint": "VARCHAR",
    "verifier_inference_fingerprint": "VARCHAR",
    "consensus_fingerprint": "VARCHAR",
    "automation_profile_id": "VARCHAR",
}

PARSE_ERROR_COLUMNS = {
    "error_id": "VARCHAR",
    "proposition_id": "VARCHAR",
    "market_id": "VARCHAR",
    "error_kind": "VARCHAR",
    "error_message": "VARCHAR",
    "cache_state": "VARCHAR",
    "error_type": "VARCHAR",
    "status_code": "INTEGER",
    "response_json": "VARCHAR",
    "question": "VARCHAR",
    "description": "VARCHAR",
    "parse_confidence": "DOUBLE",
    "market_source_hash": "VARCHAR",
    "model_role": "VARCHAR",
    "parser_model": "VARCHAR",
    "prompt_version": "VARCHAR",
    "schema_version": "VARCHAR",
    "normalization_version": "VARCHAR",
}

MARKET_STATE_COLUMNS = {
    "market_id": "VARCHAR",
    "source_hash": "VARCHAR",
    "parse_model": "VARCHAR",
    "parse_prompt_version": "VARCHAR",
    "normalization_version": "VARCHAR",
    "rule_version": "VARCHAR",
}

PROPOSITION_FINGERPRINT_COLUMNS = {
    "proposition_id": "VARCHAR",
    "market_id": "VARCHAR",
    "market_source_hash": "VARCHAR",
    "parse_fingerprint": "VARCHAR",
    "normalization_version": "VARCHAR",
}

CANDIDATE_COMPONENT_STATE_COLUMNS = {
    "component_id": "VARCHAR",
    "component_fingerprint": "VARCHAR",
    "pair_count": "INTEGER",
    "candidate_version": "VARCHAR",
}

SOLVER_COMPONENT_STATE_COLUMNS = {
    "solver_component_id": "VARCHAR",
    "proposal_hash": "VARCHAR",
    "accepted_proposal_ids": "VARCHAR[]",
    "rejected_proposal_ids": "VARCHAR[]",
    "proposal_count": "INTEGER",
    "hard_clause_count": "INTEGER",
    "soft_clause_count": "INTEGER",
    "objective_cost": "BIGINT",
    "solver_version": "VARCHAR",
    "constraint_version": "VARCHAR",
}

NODE_COLUMNS = {
    "node_id": "VARCHAR",
    "market_id": "VARCHAR",
    "outcome_index": "INTEGER",
    "clob_token_id": "VARCHAR",
    "question": "VARCHAR",
    "outcome_label": "VARCHAR",
    "event_slug": "VARCHAR",
    "is_active": "BOOLEAN",
    "is_closed": "BOOLEAN",
    "market_family": "VARCHAR",
    "canonical_proposition": "VARCHAR",
    "proposition_type": "VARCHAR",
    "expected_tokens": "INTEGER",
    "first_seen_ts": "TIMESTAMPTZ",
    "last_seen_ts": "TIMESTAMPTZ",
}

MARKET_GROUP_COLUMNS = {
    "market_id": "VARCHAR",
    "event_slug": "VARCHAR",
    "question": "VARCHAR",
    "market_family": "VARCHAR",
    "num_tokens": "INTEGER",
    "token_ids": "VARCHAR[]",
    "outcome_labels": "VARCHAR[]",
    "is_active": "BOOLEAN",
    "is_closed": "BOOLEAN",
    "first_seen_ts": "TIMESTAMPTZ",
    "last_seen_ts": "TIMESTAMPTZ",
}

LOGIC_EDGE_COLUMNS = {
    name: (
        "DOUBLE"
        if name == "confidence"
        else "VARCHAR[]" if name == "assumptions" else "VARCHAR"
    )
    for name in ARTIFACT_COLUMNS["logic_edges.parquet"]
}


@dataclass(frozen=True)
class _InferenceContext:
    primary_manifest: ModelManifest
    verifier_manifest: ModelManifest
    profile: AutomationProfile | None
    fingerprints: dict[str, str]
    primary_client: StructuredClient | None
    verifier_client: StructuredClient | None
    owns_primary_client: bool
    owns_verifier_client: bool
    qualification_cases: tuple[dict[str, Any], ...] = ()
    qualification_report: dict[str, Any] | None = None

def _prepare_inference_context(
    config: DiscoveryConfig,
    out_dir: Path,
    injected_primary_client: StructuredClient | None,
    injected_verifier_client: StructuredClient | None,
) -> _InferenceContext:
    provenance_root = (
        config.incremental_from.resolve()
        if config.offline and config.incremental_from is not None
        else out_dir
    )
    primary_manifest, primary_origin = _role_manifest(
        role="primary",
        configured_path=config.primary_model_manifest,
        fallback_path=provenance_root / "primary_model_manifest.json",
        configured_model=config.primary_model,
        configured_origin=config.primary_base_url,
        config=config,
        injected_client=injected_primary_client,
    )
    verifier_manifest, verifier_origin = _role_manifest(
        role="verifier",
        configured_path=config.verifier_model_manifest,
        fallback_path=provenance_root / "verifier_model_manifest.json",
        configured_model=config.verifier_model,
        configured_origin=config.verifier_base_url,
        config=config,
        injected_client=injected_verifier_client,
    )
    validate_consensus_model_pair(primary_manifest, verifier_manifest)
    fingerprints = consensus_inference_fingerprints(
        config,
        primary_manifest,
        verifier_manifest,
    )
    profile = None
    if config.offline and (provenance_root / "automation_profile.json").is_file():
        profile = load_automation_profile(provenance_root / "automation_profile.json")
        validate_automation_profile_match(
            profile,
            primary_manifest,
            verifier_manifest,
            fingerprints,
            {
                "parse": parse_request_hash(),
                "classify": classify_request_hash(),
            },
            retrieval_fingerprint=qualification_retrieval_fingerprint(config),
            parse_prompt_hash=_text_hash(_PARSE_PROMPT),
            parse_schema_hash=_model_schema_hash(ParsedMarket),
            classify_prompt_hash=_text_hash(_CLASSIFY_PROMPT),
            classify_schema_hash=_model_schema_hash(AtomicPairAssessment),
        )
    primary_client, owns_primary = _role_client(
        config,
        primary_origin,
        primary_manifest,
        config.primary_model,
        injected_primary_client,
    )
    try:
        verifier_client, owns_verifier = _role_client(
            config,
            verifier_origin,
            verifier_manifest,
            config.verifier_model,
            injected_verifier_client,
        )
    except Exception:
        if owns_primary and primary_client is not None:
            _run_async(primary_client.aclose())
        raise
    return _InferenceContext(
        primary_manifest=primary_manifest,
        verifier_manifest=verifier_manifest,
        profile=profile,
        fingerprints=fingerprints,
        primary_client=primary_client,
        verifier_client=verifier_client,
        owns_primary_client=owns_primary,
        owns_verifier_client=owns_verifier,
    )


def _role_manifest(
    *,
    role: str,
    configured_path: Path | None,
    fallback_path: Path,
    configured_model: str,
    configured_origin: str,
    config: DiscoveryConfig,
    injected_client: StructuredClient | None,
) -> tuple[ModelManifest, str]:
    path = configured_path
    if path is None and config.offline and fallback_path.is_file():
        path = fallback_path
    if path is not None:
        manifest = load_model_manifest(path)
        origin = manifest.inference_origin if config.offline else normalize_inference_base_url(
            configured_origin,
            allow_remote=config.allow_remote_inference,
        )
        if not config.offline and origin != manifest.inference_origin:
            raise ValueError(f"Configured {role} endpoint does not match its manifest")
        if configured_model not in {manifest.model_id, manifest.loaded_model_identifier}:
            raise ValueError(f"Configured {role} model does not match its manifest")
        return manifest, origin
    if injected_client is None:
        flag = f"--{role}-model-manifest"
        raise ValueError(f"{flag} is required for {'offline' if config.offline else 'online'} discovery")
    origin = normalize_inference_base_url(
        configured_origin,
        allow_remote=config.allow_remote_inference,
    )
    synthetic_hash = _text_hash(f"injected-{role}-self-hosted-test-model")
    content = {
        "model_id": configured_model,
        "upstream_revision": "test-fixture",
        "artifact_sha256": synthetic_hash,
        "artifact_kind": "fixture",
        "quantization": "fixture",
        "license": "Apache-2.0",
        "tokenizer_sha256": synthetic_hash,
        "chat_template_sha256": synthetic_hash,
        "runtime": "llama.cpp",
        "runtime_version": "test-fixture",
        "loaded_model_identifier": configured_model,
        "context_length": 8192,
        "deployment": f"in-process {role} network-free test fixture",
        "inference_origin": origin,
    }
    return ModelManifest.model_validate(
        {"manifest_id": canonical_json_sha256(content), **content}
    ), origin


def _role_client(
    config: DiscoveryConfig,
    origin: str,
    manifest: ModelManifest,
    model: str,
    injected: StructuredClient | None,
) -> tuple[StructuredClient | None, bool]:
    if config.offline:
        return None, False
    client = injected or LocalStructuredClient(
        origin,
        allow_remote=config.allow_remote_inference,
    )
    owns = injected is None
    preflight = _run_async(
        _with_retries(
            lambda: client.preflight(
                expected_model=model,
                expected_runtime=manifest.runtime,
            )
        )
    )
    runtime_version = preflight.get("runtime_version")
    if runtime_version and runtime_version != manifest.runtime_version:
        if owns:
            _run_async(client.aclose())
        raise ValueError("Endpoint runtime version does not match the model manifest")
    return client, owns


def _ensure_automation_profile(
    input_path: Path,
    out_dir: Path,
    selected_markets: Sequence[SourceMarket],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    *,
    injected: bool,
    embedder: Callable[[list[str], DiscoveryConfig], Any],
) -> _InferenceContext:
    fixture_qualification = (
        injected and _sha256(input_path) != CANONICAL_CATALOG_SHA256
    )
    if (
        config.offline
        and inference.profile is not None
        and bool(inference.profile.metrics.get("fixture"))
    ):
        return replace(
            inference,
            qualification_report=_qualification_report(inference.profile),
        )
    if fixture_qualification:
        cases: list[dict[str, Any]] = []
        case_set_hash = _text_hash("network-free-qualification-fixture")
        evaluation_metrics: dict[str, Any] = {
            "fixture": True,
            "semantic_accuracy_claim": False,
        }
        gates = {"network_free_fixture": True}
        thresholds = {relation: 0.0 for relation in config.relation_thresholds}
        status = "AUTOMATION_VALIDATED"
    else:
        _, _, all_markets, _ = _load_source_markets(input_path)
        cases = generate_qualification_cases(
            all_markets,
            seed=config.sampling_seed,
        )
        case_set_hash = qualification_case_set_hash(cases)
        profile_key = _automation_profile_key(case_set_hash, inference, config)
        cached_profile = cache.get_qualification_profile(profile_key)
        if cached_profile is not None:
            profile = AutomationProfile.model_validate(cached_profile)
            validate_automation_profile_match(
                profile,
                inference.primary_manifest,
                inference.verifier_manifest,
                inference.fingerprints,
                {
                    "parse": parse_request_hash(),
                    "classify": classify_request_hash(),
                },
                retrieval_fingerprint=qualification_retrieval_fingerprint(config),
                parse_prompt_hash=_text_hash(_PARSE_PROMPT),
                parse_schema_hash=_model_schema_hash(ParsedMarket),
                classify_prompt_hash=_text_hash(_CLASSIFY_PROMPT),
                classify_schema_hash=_model_schema_hash(AtomicPairAssessment),
            )
            if profile.case_set_hash != case_set_hash:
                raise ValueError("Cached automation profile case set does not match")
            if (
                inference.profile is not None
                and inference.profile.profile_id != profile.profile_id
            ):
                raise ValueError(
                    "Offline output and cache automation profiles do not match"
                )
            return replace(
                inference,
                profile=profile,
                qualification_cases=tuple(cases),
                qualification_report=_qualification_report(profile),
            )
        if config.offline:
            raise ValueError(
                "Offline discovery cache is missing an exact automation profile"
            )
        predictions = _run_qualification_cases(
            cases,
            config,
            cache,
            inference,
            embedder,
        )
        evaluation = evaluate_qualification(cases, predictions)
        evaluation_metrics = evaluation.metrics
        gates = evaluation.gates
        thresholds = evaluation.thresholds
        status = evaluation.status

    content = {
        "status": status,
        "case_set_hash": case_set_hash,
        "qualification_generator_version": QUALIFICATION_GENERATOR_VERSION,
        "retrieval_fingerprint": qualification_retrieval_fingerprint(config),
        "primary_manifest_id": inference.primary_manifest.manifest_id,
        "primary_manifest_sha256": manifest_sha256(inference.primary_manifest),
        "verifier_manifest_id": inference.verifier_manifest.manifest_id,
        "verifier_manifest_sha256": manifest_sha256(inference.verifier_manifest),
        "parse_prompt_hash": _text_hash(_PARSE_PROMPT),
        "parse_schema_hash": _model_schema_hash(ParsedMarket),
        "classify_prompt_hash": _text_hash(_CLASSIFY_PROMPT),
        "classify_schema_hash": _model_schema_hash(AtomicPairAssessment),
        "request_contract_hashes": {
            "parse": parse_request_hash(),
            "classify": classify_request_hash(),
        },
        "inference_fingerprints": inference.fingerprints,
        "relations": {
            relation: {
                "enabled": status == "AUTOMATION_VALIDATED",
                "threshold": float(thresholds[relation]),
                "support": int(
                    evaluation_metrics.get("relations", {})
                    .get(relation, {})
                    .get("correct", 500 if fixture_qualification else 0)
                ),
                "precision": float(
                    evaluation_metrics.get("relations", {})
                    .get(relation, {})
                    .get("precision", 1.0 if fixture_qualification else 0.0)
                ),
            }
            for relation in config.relation_thresholds
        },
        "structured_output_validity": {
            "primary": float(
                evaluation_metrics.get("primary_structured_validity", 1.0)
            ),
            "verifier": float(
                evaluation_metrics.get("verifier_structured_validity", 1.0)
            ),
        },
        "metrics": {
            **evaluation_metrics,
            "gates": gates,
            "semantic_accuracy_claim": False,
        },
    }
    profile = AutomationProfile.model_validate(
        {"profile_id": canonical_json_sha256(content), **content}
    )
    report = _qualification_report(profile)
    if not fixture_qualification:
        cache.put_qualification_profile(
            _automation_profile_key(case_set_hash, inference, config),
            profile.model_dump(mode="json"),
        )
    if profile.status != "AUTOMATION_VALIDATED":
        failure_dir = Path(str(out_dir) + ".qualification-failure")
        failure_staging = Path(
            tempfile.mkdtemp(
                prefix=f".{failure_dir.name}.",
                dir=failure_dir.parent,
            )
        )
        try:
            _write_json_atomic(
                failure_staging / "primary_model_manifest.json",
                inference.primary_manifest.model_dump(mode="json"),
            )
            _write_json_atomic(
                failure_staging / "verifier_model_manifest.json",
                inference.verifier_manifest.model_dump(mode="json"),
            )
            _write_json_atomic(
                failure_staging / "automation_profile.json",
                profile.model_dump(mode="json"),
            )
            _write_json_atomic(
                failure_staging / "qualification_report.json",
                report,
            )
            failure_db = DuckDB()
            try:
                _create_and_fill(
                    failure_db,
                    "qualification_cases_v",
                    QUALIFICATION_CASE_COLUMNS,
                    cases,
                )
                _copy_table(
                    failure_db,
                    "qualification_cases_v",
                    failure_staging / "qualification_cases.parquet",
                    list(QUALIFICATION_CASE_COLUMNS),
                    "case_id",
                )
            finally:
                failure_db.close()
            publish_directory_atomically(failure_staging, failure_dir).finalize()
        finally:
            shutil.rmtree(failure_staging, ignore_errors=True)
        raise RuntimeError("Automated qualification did not produce AUTOMATION_VALIDATED")
    return replace(
        inference,
        profile=profile,
        qualification_cases=tuple(cases),
        qualification_report=report,
    )


def _qualification_report(profile: AutomationProfile) -> dict[str, Any]:
    metrics = dict(profile.metrics)
    gates = metrics.pop("gates", {})
    return {
        "schema_version": AUTOMATION_PROFILE_SCHEMA_VERSION,
        "status": profile.status,
        "profile_id": profile.profile_id,
        "case_set_hash": profile.case_set_hash,
        "gates": gates,
        "metrics": metrics,
        "semantic_accuracy_claim": False,
        "qualification_kind": "catalog_derived_automated",
    }


def _automation_profile_key(
    case_set_hash: str,
    inference: _InferenceContext,
    config: DiscoveryConfig,
) -> str:
    return canonical_json_sha256(
        {
            "schema_version": AUTOMATION_PROFILE_SCHEMA_VERSION,
            "case_set_hash": case_set_hash,
            "generator": QUALIFICATION_GENERATOR_VERSION,
            "retrieval": qualification_retrieval_fingerprint(config),
            "primary_manifest": manifest_sha256(inference.primary_manifest),
            "verifier_manifest": manifest_sha256(inference.verifier_manifest),
            "fingerprints": inference.fingerprints,
        }
    )


def _run_qualification_cases(
    cases: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    embedder: Callable[[list[str], DiscoveryConfig], Any],
) -> list[QualificationPrediction]:
    retrieved_case_ids = qualification_retrieved_case_ids(
        cases,
        config,
        embedder,
    )
    primary_entries = _qualification_role_entries(
        cases,
        role="primary",
        config=config,
        cache=cache,
        inference=inference,
    )
    verifier_entries = _qualification_role_entries(
        cases,
        role="verifier",
        config=config,
        cache=cache,
        inference=inference,
    )
    predictions: list[QualificationPrediction] = []
    for case in cases:
        case_id = str(case["case_id"])
        payload = json.loads(str(case["payload_json"]))
        primary_entry = primary_entries[case_id]
        verifier_entry = verifier_entries[case_id]
        if case["record_type"] == "parse":
            predictions.append(
                _qualification_parse_prediction(
                    case,
                    payload,
                    primary_entry,
                    verifier_entry,
                )
            )
        else:
            predictions.append(
                _qualification_pair_prediction(
                    case,
                    payload,
                    primary_entry,
                    verifier_entry,
                )
            )
    stability_cases = _qualification_stability_cases(cases)
    stability = _qualification_seed_stability(
        stability_cases,
        config,
        cache,
        inference,
        primary_seed_zero=(
            primary_entries if config.sampling_seed == 0 else None
        ),
        verifier_seed_zero=(
            verifier_entries if config.sampling_seed == 0 else None
        ),
    )
    return [
        row.model_copy(
            update={
                "stability_sampled": row.case_id in stability,
                "stable_across_seeds": stability.get(row.case_id, True),
                "retrieved": (
                    row.record_type != "pair" or row.case_id in retrieved_case_ids
                ),
            }
        )
        for row in predictions
    ]


def _qualification_stability_cases(
    cases: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for relation in ("complement", "equivalent", "mutually_exclusive", "implies", "compatible"):
        relation_rows = sorted(
            (
                row
                for row in cases
                if row["record_type"] == "pair"
                and row["partition"] == "validation"
                and row["expected_relation"] == relation
            ),
            key=lambda row: str(row["case_id"]),
        )
        if len(relation_rows) < 100:
            raise ValueError(
                f"Qualification stability requires 100 validation {relation} cases"
            )
        selected.extend(relation_rows[:100])
    return sorted(selected, key=lambda row: str(row["case_id"]))


def _qualification_seed_stability(
    cases: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    *,
    primary_seed_zero: dict[str, dict[str, Any]] | None,
    verifier_seed_zero: dict[str, dict[str, Any]] | None,
) -> dict[str, bool]:
    relations_by_seed: dict[int, dict[str, str | None]] = {}
    for seed in (0, 1, 2):
        primary_entries = (
            primary_seed_zero
            if seed == 0 and primary_seed_zero is not None
            else _qualification_role_entries(
                cases,
                role="primary",
                config=config,
                cache=cache,
                inference=inference,
                sampling_seed=seed,
            )
        )
        verifier_entries = (
            verifier_seed_zero
            if seed == 0 and verifier_seed_zero is not None
            else _qualification_role_entries(
                cases,
                role="verifier",
                config=config,
                cache=cache,
                inference=inference,
                sampling_seed=seed,
            )
        )
        relations: dict[str, str | None] = {}
        for case in cases:
            case_id = str(case["case_id"])
            payload = cast(dict[str, Any], json.loads(str(case["payload_json"])))
            pair_id = str(payload["pair_id"])
            primary = _validated_qualification_assessment(
                primary_entries[case_id], pair_id
            )
            verifier = _validated_qualification_assessment(
                verifier_entries[case_id], pair_id
            )
            consensus = relation_consensus(
                primary,
                verifier,
                cast(dict[str, Any], payload["proposition_A"]),
                cast(dict[str, Any], payload["proposition_B"]),
                nli_veto=False,
            )
            relations[case_id] = (
                consensus.relation if consensus.status == "agreed" else None
            )
        relations_by_seed[seed] = relations
    return {
        str(case["case_id"]): len(
            {
                relations_by_seed[seed][str(case["case_id"])]
                for seed in (0, 1, 2)
            }
        )
        == 1
        for case in cases
    }


def _qualification_role_entries(
    cases: Sequence[dict[str, Any]],
    *,
    role: Literal["primary", "verifier"],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    sampling_seed: int | None = None,
) -> dict[str, dict[str, Any]]:
    effective_config = (
        config
        if sampling_seed is None
        else replace(config, sampling_seed=sampling_seed)
    )
    model = config.primary_model if role == "primary" else config.verifier_model
    client = inference.primary_client if role == "primary" else inference.verifier_client
    if client is None:
        raise RuntimeError(f"Online qualification requires the {role} model endpoint")
    requests: list[tuple[str, str, str, dict[str, object]]] = []
    for case in cases:
        task_kind = "parse" if case["record_type"] == "parse" else "classify"
        task = (
            f"qualification_{role}_{task_kind}"
            if sampling_seed is None
            else f"qualification_stability_{role}_{task_kind}_seed_{sampling_seed}"
        )
        if sampling_seed is None:
            fingerprint = inference.fingerprints[f"{role}_{task_kind}"]
        else:
            role_manifest = (
                inference.primary_manifest
                if role == "primary"
                else inference.verifier_manifest
            )
            fingerprint = inference_fingerprint(
                role_manifest,
                role=f"qualification_stability_{role}_{task_kind}",
                requested_model=model,
                prompt_version=(
                    PARSE_PROMPT_VERSION
                    if task_kind == "parse"
                    else CLASSIFY_PROMPT_VERSION
                ),
                prompt_hash=_text_hash(
                    _PARSE_PROMPT if task_kind == "parse" else _CLASSIFY_PROMPT
                ),
                request_schema_hash=(
                    parse_request_hash()
                    if task_kind == "parse"
                    else classify_request_hash()
                ),
                schema_hash=_model_schema_hash(
                    ParsedMarket
                    if task_kind == "parse"
                    else AtomicPairAssessment
                ),
                settings=_generation_settings(
                    effective_config,
                    role=cast(Literal["parse", "classify"], task_kind),
                ),
            )
        prompt_version = PARSE_PROMPT_VERSION if task_kind == "parse" else CLASSIFY_PROMPT_VERSION
        prompt = _PARSE_PROMPT if task_kind == "parse" else _CLASSIFY_PROMPT
        response_model = ParsedMarket if task_kind == "parse" else AtomicPairAssessment
        payload = cast(dict[str, object], json.loads(str(case["payload_json"])))
        key = cache.key(
            task,
            fingerprint,
            prompt_version,
            _text_hash(prompt),
            _model_schema_hash(response_model),
            payload,
        )
        requests.append((str(case["case_id"]), key, task_kind, payload))
    cached_by_key = cache.get_many([row[1] for row in requests])
    missing = [row for row in requests if row[1] not in cached_by_key]
    if missing:
        async def call(payload: dict[str, object], kind: str) -> Any:
            response_model = ParsedMarket if kind == "parse" else AtomicPairAssessment
            return await _with_retries(
                lambda: client.generate(
                    model=model,
                    system_prompt=_PARSE_PROMPT if kind == "parse" else _CLASSIFY_PROMPT,
                    payload=payload,
                    response_model=response_model,
                    settings=_generation_settings(effective_config, role=cast(Literal["parse", "classify"], kind)),
                )
            )

        async def run_chunk(
            rows: Sequence[tuple[str, str, str, dict[str, object]]],
        ) -> list[tuple[tuple[str, str, str, dict[str, object]], Any]]:
            semaphore = asyncio.Semaphore(config.llm_concurrency)

            async def run_one(row: tuple[str, str, str, dict[str, object]]) -> tuple[tuple[str, str, str, dict[str, object]], Any]:
                async with semaphore:
                    try:
                        return row, await call(row[3], row[2])
                    except Exception as exc:
                        return row, exc

            return await asyncio.gather(*(run_one(row) for row in rows))

        for start in range(0, len(missing), 256):
            pending: dict[str, dict[str, Any]] = {}
            for row, result in _run_async(
                run_chunk(missing[start : start + 256])
            ):
                _, key, task_kind, payload = row
                task = (
                    f"qualification_{role}_{task_kind}"
                    if sampling_seed is None
                    else f"qualification_stability_{role}_{task_kind}_seed_{sampling_seed}"
                )
                if isinstance(result, Exception):
                    entry = cache_entry(
                        task=task,
                        parsed=None,
                        error=str(result),
                        observed_model=model,
                        usage={},
                        usage_scope=None,
                        state=(
                            "transient_failure"
                            if _is_transient_error(result)
                            else "stable_failure"
                        ),
                        error_type=type(result).__name__,
                        status_code=getattr(result, "status_code", None),
                    )
                else:
                    entry = cache_entry(
                        task=task,
                        parsed=result.parsed.model_dump(mode="json"),
                        error=None,
                        observed_model=str(result.observed_model),
                        usage=dict(result.usage),
                        usage_scope=cache.usage_scope(task, [payload]),
                        state="success",
                    )
                pending[key] = entry
            cache.put_many(pending)
            cached_by_key.update(pending)
    return {case_id: cached_by_key[key] for case_id, key, _, _ in requests}


def _qualification_parse_prediction(
    case: dict[str, Any],
    payload: dict[str, Any],
    primary_entry: dict[str, Any],
    verifier_entry: dict[str, Any],
) -> QualificationPrediction:
    source_market = SourceMarket(
        market_id=str(payload["market_id"]),
        question=str(payload["question"]),
        description=str(payload.get("description") or ""),
        source_hash=str(payload["market_source_hash"]),
        event_id=_str_or_none(payload.get("event_id")),
        event_slug=_str_or_none(payload.get("event_slug")),
        category=_str_or_none(payload.get("category")),
        tags=tuple(str(value) for value in payload.get("tags") or []),
        time_start=None,
        time_end=None,
        outcomes=tuple(
            SourceOutcome(index, str(row["outcome"]), str(row["clob_token_id"]))
            for index, row in enumerate(payload["outcomes"])
        ),
    )
    primary = _validated_qualification_parse(primary_entry, source_market)
    verifier = _validated_qualification_parse(verifier_entry, source_market)
    consensus = merge_parsed_markets(source_market, primary, verifier)
    agreed = all(row.status == "agreed" for row in consensus.values())
    fields = (
        "subject", "predicate", "object", "operator", "threshold", "unit",
        "time_start", "time_end", "competition", "event_scope", "jurisdiction", "polarity",
    )
    field_scores = {field: 0.0 for field in fields}
    primary_by_outcome = {
        row.outcome: row for row in (primary.propositions if primary else [])
    }
    verifier_by_outcome = {
        row.outcome: row for row in (verifier.propositions if verifier else [])
    }
    for outcome in source_market.outcomes:
        first = primary_by_outcome.get(outcome.outcome)
        second = verifier_by_outcome.get(outcome.outcome)
        if first is None or second is None:
            continue
        for field in fields:
            if field == "subject":
                field_scores[field] += _subject_f1(first.subject, second.subject)
            else:
                field_scores[field] += float(
                    _normalized_qualification_field(field, getattr(first, field))
                    == _normalized_qualification_field(field, getattr(second, field))
                )
    denominator = max(1, len(source_market.outcomes))
    field_agreement = {
        field: score / denominator for field, score in field_scores.items()
    }
    authoritative_conflict = _qualification_authoritative_conflict(
        payload,
        primary,
    ) or _qualification_authoritative_conflict(payload, verifier)
    return QualificationPrediction(
        case_id=str(case["case_id"]),
        record_type="parse",
        partition=cast(Literal["selection", "validation"], case["partition"]),
        expected_relation=None,
        primary_structured_valid=primary is not None,
        verifier_structured_valid=verifier is not None,
        id_coverage=primary is not None and verifier is not None,
        authoritative_conflict=authoritative_conflict,
        parse_agreed=agreed,
        field_agreement=field_agreement,
        primary_relation=None,
        verifier_relation=None,
        consensus_relation=None,
        consensus_confidence=None,
        citations_valid=True,
        assumptions_empty=True,
        nli_veto=False,
    )


def _subject_f1(first: Sequence[str], second: Sequence[str]) -> float:
    left = {canonical_entity(value) for value in first if value.strip()}
    right = {canonical_entity(value) for value in second if value.strip()}
    if not left and not right:
        return 1.0
    overlap = len(left & right)
    precision = overlap / max(1, len(left))
    recall = overlap / max(1, len(right))
    return 2 * precision * recall / max(1e-12, precision + recall)


def _normalized_qualification_field(field: str, value: object) -> object:
    if field == "unit" and isinstance(value, str):
        return canonical_unit(value)
    if field in {"object", "competition", "event_scope", "jurisdiction"}:
        return canonical_entity(value) if isinstance(value, str) else None
    if field == "predicate":
        return normalize_optional(value) if isinstance(value, str) else None
    if field in {"time_start", "time_end"}:
        from .input import utc_datetime

        return utc_datetime(cast(Any, value))
    return value


def _qualification_authoritative_conflict(
    payload: dict[str, Any],
    parsed: ParsedMarket | None,
) -> bool:
    if parsed is None:
        return False
    by_outcome = {row.outcome: row for row in parsed.propositions}
    for source_outcome in payload.get("outcomes", []):
        if not isinstance(source_outcome, dict):
            return True
        parsed_outcome = by_outcome.get(str(source_outcome.get("outcome")))
        if parsed_outcome is None:
            return True
        extraction = source_outcome.get("authoritative_extraction")
        if not isinstance(extraction, dict):
            continue
        for field in ("polarity", "operator", "threshold", "unit"):
            expected = extraction.get(field)
            observed = getattr(parsed_outcome, field)
            if expected is None or observed is None:
                continue
            if _normalized_qualification_field(field, observed) != expected:
                return True
    return False


def _validated_qualification_parse(
    entry: dict[str, Any], source: SourceMarket
) -> ParsedMarket | None:
    if cache_error(entry) or entry.get("parsed") is None:
        return None
    try:
        parsed = ParsedMarket.model_validate(entry["parsed"])
        _validate_parsed_market(source, parsed)
        return parsed
    except (TypeError, ValueError):
        return None


def _qualification_pair_prediction(
    case: dict[str, Any],
    payload: dict[str, Any],
    primary_entry: dict[str, Any],
    verifier_entry: dict[str, Any],
) -> QualificationPrediction:
    proposition_a = cast(dict[str, Any], payload["proposition_A"])
    proposition_b = cast(dict[str, Any], payload["proposition_B"])
    primary = _validated_qualification_assessment(primary_entry, str(payload["pair_id"]))
    verifier = _validated_qualification_assessment(verifier_entry, str(payload["pair_id"]))
    primary_relation = _derive_atomic_relation(primary)[0] if primary is not None else None
    verifier_relation = _derive_atomic_relation(verifier)[0] if verifier is not None else None
    consensus = relation_consensus(
        primary,
        verifier,
        proposition_a,
        proposition_b,
        nli_veto=False,
    )
    citations_valid = consensus.status not in {"invalid_citation", "inference_failure"}
    assumptions_empty = bool(
        primary is not None and verifier is not None
        and not primary.assumptions and not verifier.assumptions
    )
    return QualificationPrediction(
        case_id=str(case["case_id"]),
        record_type="pair",
        partition=cast(Literal["selection", "validation"], case["partition"]),
        expected_relation=_str_or_none(case.get("expected_relation")),
        primary_structured_valid=primary is not None,
        verifier_structured_valid=verifier is not None,
        id_coverage=primary is not None and verifier is not None,
        authoritative_conflict=False,
        parse_agreed=None,
        primary_relation=primary_relation,
        verifier_relation=verifier_relation,
        consensus_relation=consensus.relation,
        consensus_confidence=consensus.confidence,
        citations_valid=citations_valid,
        assumptions_empty=assumptions_empty,
        nli_veto=False,
    )


def _validated_qualification_assessment(
    entry: dict[str, Any], pair_id: str
) -> AtomicPairAssessment | None:
    if cache_error(entry) or entry.get("parsed") is None:
        return None
    try:
        parsed = AtomicPairAssessment.model_validate(entry["parsed"])
        return parsed if parsed.pair_id == pair_id else None
    except (TypeError, ValueError):
        return None


def discover(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig | None = None,
    _primary_client: StructuredClient | None = None,
    _verifier_client: StructuredClient | None = None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None = None,
    _nli_scorer: NliScorer | None = None,
) -> dict[str, object]:
    with ExitStack() as resources:
        return _discover_impl(
            input_path,
            out_dir,
            config=config,
            _primary_client=_primary_client,
            _verifier_client=_verifier_client,
            _embedder=_embedder,
            _nli_scorer=_nli_scorer,
            resources=resources,
        )


def _discover_impl(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig | None,
    _primary_client: StructuredClient | None,
    _verifier_client: StructuredClient | None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None,
    _nli_scorer: NliScorer | None,
    resources: ExitStack,
) -> dict[str, object]:
    config = config or DiscoveryConfig()
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"Input parquet does not exist: {input_path}")
    config.validate()

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    recorder = StageRecorder(config.progress_format)
    progress_token = _ACTIVE_PROGRESS.set(recorder)
    resources.callback(_ACTIVE_PROGRESS.reset, progress_token)
    inference = recorder.run(
        "model_preflight",
        lambda: _prepare_inference_context(
            config,
            out_dir,
            _primary_client,
            _verifier_client,
        ),
    )
    if inference.owns_primary_client and inference.primary_client is not None:
        resources.callback(_close_structured_client, inference.primary_client)
    if inference.owns_verifier_client and inference.verifier_client is not None:
        resources.callback(_close_structured_client, inference.verifier_client)

    source_schema, input_rows, markets, input_selection = recorder.run(
        "normalize_input",
        lambda: _load_source_markets(
            input_path,
            max_propositions=config.max_propositions,
        ),
    )
    cache_dir = (config.cache_dir or Path(str(out_dir) + ".cache")).resolve()
    cache = InferenceCache(cache_dir, offline=config.offline)
    resources.callback(cache.close)
    inference = recorder.run(
        "automated_qualification",
        lambda: _ensure_automation_profile(
            input_path,
            out_dir,
            markets,
            config,
            cache,
            inference,
            injected=(
                _primary_client is not None and _verifier_client is not None
            ),
            embedder=_embedder or _embed_texts,
        ),
    )
    recorder.event(
        "qualification_complete",
        status=(inference.profile.status if inference.profile else "QUALIFICATION_FAILED"),
        case_count=len(inference.qualification_cases),
    )
    incremental = recorder.run(
        "prepare_incremental",
        lambda: _prepare_incremental(
            config,
            out_dir,
            markets,
            cache,
            inference,
        ),
    )
    reusable_solver_components = incremental.reusable_solver_components
    incremental_stats = incremental.stats
    incremental_resources = incremental.resources
    baseline_semantic_fingerprints = (
        incremental_resources.baseline_semantic_fingerprints
    )
    baseline_fingerprint_by_id = {
        str(row["proposition_id"]): str(row["neighborhood_fingerprint"])
        for row in baseline_semantic_fingerprints
    }
    prior_market_hashes = incremental_resources.prior_market_hashes
    prior_solver_hashes = set(incremental_resources.prior_solver_hashes)
    execution_plan = ExecutionPlan(
        incremental=bool(
            incremental_stats.get("enabled")
            or incremental_stats.get("offline_state_replay")
        )
    )
    current_market_hashes = {
        market.market_id: market.source_hash for market in markets
    }
    for market_id, source_hash in sorted(current_market_hashes.items()):
        prior_hash = prior_market_hashes.get(market_id)
        execution_plan.add(
            stage="markets",
            unit_type="market",
            unit_id=market_id,
            status="reused" if prior_hash == source_hash else "recomputed",
            invalidation_reasons=(
                [] if prior_hash == source_hash else ["source_hash_or_selection"]
            ),
            input_fingerprint=prior_hash,
            output_fingerprint=source_hash,
        )
    for market_id in sorted(set(prior_market_hashes) - set(current_market_hashes)):
        execution_plan.add(
            stage="markets",
            unit_type="market",
            unit_id=market_id,
            status="removed",
            invalidation_reasons=["selection_or_removal"],
            input_fingerprint=prior_market_hashes[market_id],
        )
    state = RunState()
    parse_result = recorder.run(
        "parse_propositions",
        lambda: _parse_propositions(
            markets,
            source_schema,
            config,
            cache,
            state,
            inference,
        ),
    )
    propositions = parse_result.propositions
    parse_assessments = parse_result.parse_assessments
    parse_quarantines = parse_result.quarantines
    _require_model_role_coverage(parse_assessments, task="parse")
    recorder.event(
        "batch_progress",
        task="dual_parse",
        completed=len(parse_assessments),
        total=len(propositions) * 2,
        quarantined=len(parse_quarantines),
    )
    rule_support = recorder.run(
        "automated_rule_gates",
        _automated_rule_gates,
    )
    enabled_rule_ids = set(rule_support["enabled"])
    reusable_candidates_path = incremental_resources.reusable_candidates_path
    prior_enabled_rules = set(incremental_resources.prior_enabled_rules)
    if (
        reusable_candidates_path is not None
        and prior_enabled_rules != enabled_rule_ids
    ):
        reusable_candidates_path = None
        incremental_stats["invalidation_reasons"] = sorted(
            {
                *incremental_stats.get("invalidation_reasons", []),
                "enabled_rule_set",
            }
        )
    baseline_candidate_blocks = incremental_resources.baseline_candidate_blocks
    baseline_candidate_reasons = incremental_resources.baseline_candidate_reasons
    baseline_embedding_state_path = (
        incremental_resources.baseline_embedding_state_path
    )
    baseline_semantic_state_path = (
        incremental_resources.baseline_semantic_state_path
    )
    semantic_execution: list[dict[str, Any]] = []
    if reusable_candidates_path is not None:
        if (
            baseline_embedding_state_path is None
            or baseline_semantic_state_path is None
            or baseline_candidate_blocks is None
            or baseline_candidate_reasons is None
        ):
            raise RuntimeError("Reusable candidate state is incomplete")
        retrieval_result = recorder.run(
            "generate_candidates",
            lambda: RetrievalStageResult(
                workspace=CandidateStore.from_parquet(
                    reusable_candidates_path,
                    block_path=baseline_candidate_blocks,
                    reason_path=baseline_candidate_reasons,
                    embedding_path=baseline_embedding_state_path,
                    neighbor_path=baseline_semantic_state_path,
                ),
                reused=True,
            ),
        )
        candidate_store = retrieval_result.workspace
        resources.callback(candidate_store.close)
        candidate_store.reset_for_run()
        candidate_store.structural_member_limit = _structural_member_limit(
            config.max_candidates
        )
        semantic_execution.extend(
            {
                "proposition_id": str(row["proposition_id"]),
                "status": "reused",
                "neighborhood_fingerprint": baseline_fingerprint_by_id.get(
                    str(row["proposition_id"]),
                    _text_hash(""),
                ),
            }
            for row in propositions
        )
        incremental_stats["candidate_generation_reused"] = (
            retrieval_result.reused
        )
    else:
        retrieval_result = recorder.run(
            "generate_candidates",
            lambda: RetrievalStageResult(
                workspace=generate_candidate_workspace(
                    propositions,
                    config,
                    _embedder or _embed_texts,
                    baseline_embedding_path=(
                        baseline_embedding_state_path
                    ),
                    baseline_neighbor_path=(
                        baseline_semantic_state_path
                    ),
                    neighborhood_execution_sink=semantic_execution,
                    baseline_candidate_blocks=baseline_candidate_blocks,
                    baseline_candidate_reasons=baseline_candidate_reasons,
                    baseline_neighborhood_fingerprints=(
                        baseline_fingerprint_by_id
                    ),
                    enabled_rule_ids=enabled_rule_ids,
                ),
                reused=False,
            ),
        )
        candidate_store = retrieval_result.workspace
        resources.callback(candidate_store.close)
        incremental_stats["candidate_generation_reused"] = (
            retrieval_result.reused
        )
    block_execution = recorder.run(
        "plan_candidate_blocks",
        candidate_store.block_execution_rows,
    )
    for row in block_execution:
        execution_plan.add(
            stage="candidate_blocks",
            unit_type="structured_block",
            unit_id=str(row["block_id"]),
            status=str(row["status"]),
            invalidation_reasons=(
                [] if row["status"] == "reused" else ["membership_fingerprint"]
            ),
            input_fingerprint=(
                str(row["input_fingerprint"])
                if row["input_fingerprint"] is not None
                else None
            ),
            output_fingerprint=(
                str(row["output_fingerprint"])
                if row["output_fingerprint"] is not None
                else None
            ),
        )
    incremental_stats["candidate_blocks_reused"] = sum(
        row["status"] == "reused" for row in block_execution
    )
    incremental_stats["candidate_blocks_recomputed"] = sum(
        row["status"] == "recomputed" for row in block_execution
    )
    incremental_stats["candidate_blocks_removed"] = sum(
        row["status"] == "removed" for row in block_execution
    )
    semantic_neighbor_fingerprints = [
        {
            "proposition_id": row["proposition_id"],
            "neighborhood_fingerprint": row["neighborhood_fingerprint"],
        }
        for row in semantic_execution
    ]
    _record_semantic_neighborhood_reuse(
        incremental_stats,
        baseline_semantic_fingerprints,
        semantic_neighbor_fingerprints,
        execution_plan,
        semantic_execution,
    )
    proposition_fingerprint_state = _proposition_fingerprint_rows(propositions)
    recorder.event(
        "retrieval_progress",
        candidates=candidate_store.stats()["candidate_edges"],
        reused=bool(incremental_stats.get("candidate_generation_reused")),
        semantic_neighborhoods_reused=int(
            incremental_stats.get("semantic_neighborhoods_reused", 0)
        ),
    )
    deterministic_candidates = recorder.run(
        "load_deterministic_proposals",
        candidate_store.deterministic_rows,
    )
    _hydrate_deterministic_candidates(
        deterministic_candidates,
        propositions,
        config,
    )
    deterministic_edges = recorder.run(
        "derive_deterministic_relations",
        lambda: _derive_deterministic_edges(
            deterministic_candidates,
            propositions,
        ),
    )
    candidate_store.mark_classification_budget(
        [
            str(row["proposition_id"])
            for row in propositions
            if row.get("parse_status") == "parsed"
        ]
    )
    nli_pool_limit = min(
        config.max_candidates,
        config.max_llm_pairs * 4,
    )
    recorder.run(
        "score_nli",
        lambda: _score_nli_candidate_batches(
            candidate_store,
            propositions,
            config,
            cache,
            inference,
            _nli_scorer,
            injected_client=(
                _primary_client is not None or _verifier_client is not None
            ),
            limit=nli_pool_limit,
        ),
    )
    classification_result = recorder.run(
        "classify_pairs",
        lambda: _classify_candidate_batches(
            candidate_store,
            propositions,
            config,
            cache,
            state,
            incremental_stats,
            incremental_resources,
            inference,
        ),
    )
    generated_edges = classification_result.edges
    model_assessments = classification_result.model_assessments
    model_quarantines = classification_result.quarantines
    _require_model_role_coverage(model_assessments, task="classification")
    recorder.event(
        "batch_progress",
        task="dual_classification",
        completed=len(model_assessments),
        total=min(
            candidate_store.stats()["candidate_edges"] * 2,
            config.max_llm_pairs * 2,
        ),
        quarantined=len(model_quarantines),
    )
    candidate_component_state = recorder.run(
        "fingerprint_candidate_components",
        lambda: candidate_store.component_rows(
            sorted(str(row["proposition_id"]) for row in propositions),
            CANDIDATE_STATE_VERSION,
        ),
    )
    _record_candidate_component_reuse(
        incremental_stats,
        candidate_component_state,
        incremental_resources.prior_candidate_components,
    )
    solving_result = recorder.run(
        "solve_consistency",
        lambda: _solve_logic_edges(
            deterministic_edges + generated_edges,
            reusable_solver_components=reusable_solver_components,
        ),
    )
    logic_edges = solving_result.accepted_edges
    rejected_edges = solving_result.rejected_edges
    solver_stats = solving_result.stats
    solver_component_state = _solver_component_state_rows(
        logic_edges,
        rejected_edges,
    )
    current_solver_hashes = {
        str(row["proposal_hash"]): str(row["solver_component_id"])
        for row in solver_component_state
    }
    for proposal_hash, component_id in sorted(current_solver_hashes.items()):
        reused = proposal_hash in prior_solver_hashes
        execution_plan.add(
            stage="solver_components",
            unit_type="proposal_component",
            unit_id=component_id,
            status="reused" if reused else "recomputed",
            invalidation_reasons=[] if reused else ["proposal_set_hash"],
            input_fingerprint=proposal_hash if reused else None,
            output_fingerprint=proposal_hash,
        )
    for proposal_hash in sorted(
        prior_solver_hashes - set(current_solver_hashes)
    ):
        execution_plan.add(
            stage="solver_components",
            unit_type="proposal_component",
            unit_id=proposal_hash,
            status="removed",
            invalidation_reasons=["proposal_set_removed"],
            input_fingerprint=proposal_hash,
        )
    execution_plan.add(
        stage="candidate_cap",
        unit_type="global_stage",
        unit_id="canonical_candidate_aggregation",
        status="required",
        invalidation_reasons=["global_cap_and_priority_order"],
    )
    execution_summary = execution_plan.manifest()
    incremental_stats["execution_plan"] = execution_summary
    incremental_stats["affected_only_verified"] = execution_summary[
        "affected_only_verified"
    ]
    quarantine_rows = _dedupe_quarantines(
        parse_quarantines + model_quarantines + solving_result.quarantines
    )
    recorder.event(
        "quarantine_summary",
        propositions=len(parse_quarantines),
        pairs=len(model_quarantines),
        total=len(quarantine_rows),
    )
    parse_error_rows = _parse_error_rows(propositions, parse_assessments)
    execution_plan_rows = execution_plan.rows()
    recorder.run(
        "stage_workspace_tables",
        lambda: _stage_workspace_tables(
            candidate_store,
            markets,
            propositions,
            logic_edges,
            rejected_edges,
            parse_error_rows,
            parse_assessments,
            model_assessments,
            quarantine_rows,
            inference.qualification_cases,
            proposition_fingerprint_state,
            candidate_component_state,
            solver_component_state,
            execution_plan_rows,
            inference,
        ),
    )

    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.discovery-", dir=out_dir.parent)
    )
    try:
        publication_result = recorder.run(
            "publish_artifacts",
            lambda: _write_discovery_artifacts(
                staging,
                markets,
                propositions,
                candidate_store,
                logic_edges,
                rejected_edges,
                quarantine_rows,
                source_schema=source_schema,
                input_rows=input_rows,
                input_selection=input_selection,
                solver_stats=solver_stats,
                rule_support=rule_support,
                candidate_member_limit=int(
                    candidate_store.structural_member_limit or 0
                ),
                incremental_stats=incremental_stats,
                config=config,
                inference=inference,
            ),
        )
        stats = publication_result.stats
        shutil.rmtree(staging / ".duckdb-spill", ignore_errors=True)
        input_hash = recorder.run("hash_input", lambda: _sha256(input_path))
        stats["runtime_seconds"] = recorder.runtime_seconds()
        stats["peak_rss_mb"] = _peak_rss_mb()
        stats["stage_metrics"] = recorder.stage_metrics
        stats["qualification_status"] = (
            inference.profile.status if inference.profile else "QUALIFICATION_FAILED"
        )
        artifact_hashes = recorder.run(
            "hash_artifacts",
            lambda: {
                name: _sha256(staging / name)
                for name in (
                    *DISCOVERY_PARQUET_ARTIFACTS,
                    "primary_model_manifest.json",
                    "verifier_model_manifest.json",
                    "automation_profile.json",
                    "qualification_report.json",
                    *(
                        ("compute_profile.json",)
                        if (staging / "compute_profile.json").is_file()
                        else ()
                    ),
                )
            },
        )
        state_hashes = recorder.run(
            "hash_incremental_state",
            lambda: {
                name: _sha256(staging / name) for name in STATE_ARTIFACTS
            },
        )
        stats["runtime_seconds"] = recorder.runtime_seconds()
        stats["peak_rss_mb"] = _peak_rss_mb()
        stats["stage_metrics"] = recorder.stage_metrics
        write_summary_report(staging, stats)
        publication_swap = recorder.run(
            "publish_files",
            lambda: publish_directory_atomically(staging, out_dir),
        )
        try:
            stats["runtime_seconds"] = recorder.runtime_seconds()
            stats["peak_rss_mb"] = _peak_rss_mb()
            stats["stage_metrics"] = recorder.stage_metrics
            manifest = _discovery_manifest(
                input_path,
                input_hash,
                artifact_hashes,
                source_schema,
                stats,
                config,
                cache,
                state,
                recorder.timings,
                state_hashes,
                inference,
            )
            discovery_publication.write_manifest_last(out_dir, manifest)
        except Exception:
            publication_swap.rollback()
            raise
        publication_swap.finalize()
        manifest_stats = manifest["stats"]
        if not isinstance(manifest_stats, dict):
            raise RuntimeError("Discovery manifest stats must be an object")
        recorder.event(
            "run_complete",
            runtime_seconds=recorder.runtime_seconds(),
            peak_rss_mb=_peak_rss_mb(),
            publication_bytes=manifest_stats.get("publication_bytes"),
            logic_edges=manifest_stats.get("logic_edges"),
            quarantined_pairs=manifest_stats.get("quarantined_pairs"),
        )
        return {str(key): value for key, value in manifest_stats.items()}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def qualify_only(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig,
    _primary_client: StructuredClient | None = None,
    _verifier_client: StructuredClient | None = None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None = None,
) -> dict[str, Any]:
    """Run catalog-derived dual-model qualification without graph publication."""
    with ExitStack() as resources:
        return _qualify_only_impl(
            input_path,
            out_dir,
            config=config,
            _primary_client=_primary_client,
            _verifier_client=_verifier_client,
            _embedder=_embedder,
            resources=resources,
        )


def _qualify_only_impl(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig,
    _primary_client: StructuredClient | None,
    _verifier_client: StructuredClient | None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None,
    resources: ExitStack,
) -> dict[str, Any]:
    config.validate()
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"Input parquet does not exist: {input_path}")
    inference = _prepare_inference_context(
        config,
        out_dir,
        _primary_client,
        _verifier_client,
    )
    if inference.owns_primary_client and inference.primary_client is not None:
        resources.callback(_close_structured_client, inference.primary_client)
    if inference.owns_verifier_client and inference.verifier_client is not None:
        resources.callback(_close_structured_client, inference.verifier_client)
    cache_dir = (config.cache_dir or Path(str(out_dir) + ".cache")).resolve()
    cache = InferenceCache(cache_dir, offline=config.offline)
    resources.callback(cache.close)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.qualification-", dir=out_dir.parent)
    )
    try:
        _, _, markets, _ = _load_source_markets(input_path)
        inference = _ensure_automation_profile(
            input_path,
            out_dir,
            markets,
            config,
            cache,
            inference,
            injected=(
                _primary_client is not None and _verifier_client is not None
            ),
            embedder=_embedder or _embed_texts,
        )
        if inference.profile is None or inference.qualification_report is None:
            raise RuntimeError("Qualification did not produce a profile")
        _write_json_atomic(
            staging / "primary_model_manifest.json",
            inference.primary_manifest.model_dump(mode="json"),
        )
        _write_json_atomic(
            staging / "verifier_model_manifest.json",
            inference.verifier_manifest.model_dump(mode="json"),
        )
        _write_json_atomic(
            staging / "automation_profile.json",
            inference.profile.model_dump(mode="json"),
        )
        _write_json_atomic(
            staging / "qualification_report.json",
            inference.qualification_report,
        )
        if config.compute_profile is not None:
            _write_json_atomic(
                staging / "compute_profile.json",
                load_compute_profile(config.compute_profile).model_dump(mode="json"),
            )
        db = DuckDB()
        try:
            _create_and_fill(
                db,
                "qualification_cases_v",
                QUALIFICATION_CASE_COLUMNS,
                inference.qualification_cases,
            )
            _copy_table(
                db,
                "qualification_cases_v",
                staging / "qualification_cases.parquet",
                list(QUALIFICATION_CASE_COLUMNS),
                "case_id",
            )
        finally:
            db.close()
        publish_directory_atomically(staging, out_dir).finalize()
        return dict(inference.qualification_report)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _prepare_incremental(
    config: DiscoveryConfig,
    out_dir: Path,
    markets: Sequence[SourceMarket],
    cache: InferenceCache,
    inference: _InferenceContext,
) -> IncrementalPreparation:
    explicit_baseline = config.incremental_from.resolve() if config.incremental_from else None
    offline_replay_baseline = (
        out_dir
        if config.offline
        and (out_dir / "build_manifest.json").is_file()
        and (out_dir / "state" / "proposition_embeddings.parquet").is_file()
        else None
    )
    baseline = explicit_baseline or offline_replay_baseline
    if baseline is None:
        if config.offline:
            raise ValueError(
                "Offline discovery cache is missing proposition embedding state; "
                "rerun online into --out first or use --incremental-from"
            )
        return IncrementalPreparation(
            reusable_solver_components={},
            stats={
                "enabled": False,
                "baseline_manifest_hash": None,
                "markets_reused": 0,
                "markets_changed": len(markets),
                "markets_removed": 0,
                "baseline_parse_entries_seeded": 0,
                "invalidation_reasons": ["clean_run"],
            },
            resources=IncrementalResources.empty(),
        )
    if explicit_baseline is not None and baseline == out_dir:
        raise ValueError("--incremental-from must be distinct from --out")
    manifest_path = baseline / "build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            "Incremental baseline is incomplete; missing " + str(manifest_path)
        )
    manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_value, dict):
        raise ValueError(
            "Incremental baseline is incompatible. Run a clean discovery and "
            "use that completed output as --incremental-from."
        )
    manifest: dict[str, Any] = {
        str(key): value for key, value in manifest_value.items()
    }
    baseline_versions = manifest.get("versions") or {}
    if (
        str(manifest.get("version") or "").split(".")[:2]
        != __version__.split(".")[:2]
        or baseline_versions.get("candidate_state") != CANDIDATE_STATE_VERSION
    ):
        raise ValueError(
            "Incremental baseline is incompatible. Run a clean discovery and "
            "use that completed output as --incremental-from."
        )
    market_state_path = baseline / "state" / "market_state.parquet"
    proposition_fingerprint_path = (
        baseline / "state" / "proposition_fingerprints.parquet"
    )
    embedding_state_path = baseline / "state" / "proposition_embeddings.parquet"
    candidate_state_path = baseline / "state" / "candidate_components.parquet"
    candidate_blocks_path = baseline / "state" / "candidate_blocks.parquet"
    candidate_reasons_path = (
        baseline / "state" / "candidate_reason_rows.parquet"
    )
    semantic_neighbor_state_path = (
        baseline / "state" / "semantic_neighbors.parquet"
    )
    solver_state_path = baseline / "state" / "solver_components.parquet"
    execution_plan_path = baseline / "state" / "execution_plan.parquet"
    propositions_path = baseline / "propositions.parquet"
    candidates_path = baseline / "relation_candidates.parquet"
    rejected_edges_path = baseline / "rejected_edges.parquet"
    required = (
        market_state_path,
        proposition_fingerprint_path,
        embedding_state_path,
        semantic_neighbor_state_path,
        candidate_state_path,
        candidate_blocks_path,
        candidate_reasons_path,
        solver_state_path,
        execution_plan_path,
        propositions_path,
        candidates_path,
        rejected_edges_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(
            "Incremental baseline is incomplete; missing " + ", ".join(missing)
        )
    state_hashes = manifest.get("state_hashes")
    artifact_hashes = manifest.get("artifact_hashes")
    if not isinstance(state_hashes, dict) or not isinstance(artifact_hashes, dict):
        raise ValueError(
            "Incremental baseline is incompatible. Run a clean discovery and "
            "use that completed output as --incremental-from."
        )
    baseline_hashes: tuple[tuple[Path, object], ...] = tuple(
        (path, state_hashes.get(path.relative_to(baseline).as_posix()))
        for path in required
        if path.is_relative_to(baseline / "state")
    ) + tuple(
        (path, artifact_hashes.get(path.name))
        for path in required
        if path.parent == baseline
    )
    if any(
        not isinstance(expected, str) or _sha256(path) != expected
        for path, expected in baseline_hashes
    ):
        raise ValueError(
            "Incremental baseline is incompatible. Run a clean discovery and "
            "use that completed output as --incremental-from."
        )
    if manifest.get("command") != "discover":
        raise ValueError("Incremental baseline is not a discovery build")

    db = DuckDB()
    try:
        db.execute("SET TimeZone = 'UTC'")
        prior_markets = {
            str(row["market_id"]): str(row["source_hash"])
            for row in db.rows(
                f"""
                SELECT market_id, source_hash
                FROM read_parquet('{q(market_state_path)}')
                """
            )
        }
        prior_propositions = (
            []
            if config.offline
            else db.rows(
                f"""
                SELECT * FROM read_parquet('{q(propositions_path)}')
                ORDER BY market_id, outcome_index
                """
            )
        )
        embedding_row_count = int(
            db.scalar(
            f"""
            SELECT count(*)
            FROM read_parquet('{q(embedding_state_path)}')
            """
            )
            or 0
        )
        semantic_neighbor_fingerprints = db.rows(
            f"""
            SELECT
                unit_id AS proposition_id,
                output_fingerprint AS neighborhood_fingerprint
            FROM read_parquet('{q(execution_plan_path)}')
            WHERE stage = 'semantic_neighborhoods'
              AND output_fingerprint IS NOT NULL
            ORDER BY proposition_id
            """
        )
        prior_candidate_components = {
            str(row["component_id"]): str(row["component_fingerprint"])
            for row in db.rows(
                f"""
                SELECT component_id, component_fingerprint
                FROM read_parquet('{q(candidate_state_path)}')
                WHERE candidate_version = '{CANDIDATE_STATE_VERSION}'
                ORDER BY component_id
                """
            )
        }
        solver_state_rows = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(solver_state_path)}')
            ORDER BY solver_component_id
            """
        )
        prior_rejected = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(rejected_edges_path)}')
            ORDER BY proposal_id
            """
        )
        prior_classifications: list[dict[str, Any]] = []
    finally:
        db.close()

    current_hashes = {market.market_id: market.source_hash for market in markets}
    unchanged_market_ids = {
        market_id
        for market_id, source_hash in current_hashes.items()
        if prior_markets.get(market_id) == source_hash
    }
    changed_market_ids = set(current_hashes) - unchanged_market_ids
    removed_market_ids = set(prior_markets) - set(current_hashes)
    models = manifest.get("models") or {}
    previous_inference = manifest.get("inference") or {}
    parse_compatible = (
        (previous_inference.get("fingerprints") or {}).get("consensus")
        == inference.fingerprints["consensus"]
    )
    seeded = 0

    embedding_manifest = models.get("embedding") or {}
    embedding_compatible = (
        embedding_manifest.get("model") == config.embedding_model
        and embedding_manifest.get("revision") == config.embedding_revision
    )
    reasons = []
    if changed_market_ids:
        reasons.append("source_hash")
    if removed_market_ids:
        reasons.append("selection_or_removal")
    if not parse_compatible:
        reasons.append("parser_model_prompt_or_schema")
    if not embedding_compatible:
        reasons.append("embedding_model_or_revision")
    versions = manifest.get("versions") or {}
    if versions.get("normalization") != NORMALIZATION_VERSION:
        reasons.append("normalization_version")
    if versions.get("rules") != RULE_VERSION:
        reasons.append("rule_version")
    if versions.get("retrieval") != RETRIEVAL_VERSION:
        reasons.append("retrieval_version")
    previous_limits = manifest.get("limits") or {}
    if previous_limits.get("relation_thresholds") != dict(
        sorted(config.relation_thresholds.items())
    ):
        reasons.append("relation_thresholds")
    if previous_limits.get("max_candidates") != config.max_candidates:
        reasons.append("max_candidates")
    candidate_compatible = (
        not changed_market_ids
        and not removed_market_ids
        and parse_compatible
        and embedding_compatible
        and versions.get("normalization") == NORMALIZATION_VERSION
        and versions.get("rules") == RULE_VERSION
        and versions.get("retrieval") == RETRIEVAL_VERSION
        and previous_limits.get("parse_confidence") == config.parse_confidence
        and previous_limits.get("top_k") == config.top_k
        and previous_limits.get("max_candidates") == config.max_candidates
    )
    classification_compatible = (
        (previous_inference.get("fingerprints") or {}).get("consensus")
        == inference.fingerprints["consensus"]
    )
    if not classification_compatible:
        reasons.append("classifier_model_prompt_or_schema")
    previous_profile_id = previous_inference.get("automation_profile_id")
    current_profile_id = (
        inference.profile.profile_id if inference.profile is not None else None
    )
    if previous_profile_id != current_profile_id:
        reasons.append("automation_profile")
    previous_nli = models.get("nli") or {}
    if (
        previous_nli.get("model") != config.nli_model
        or previous_nli.get("revision") != config.nli_revision
    ):
        reasons.append("nli_model_or_revision")
    rejected_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prior_rejected:
        rejected_by_component[str(row["solver_component_id"])].append(row)
    reusable_solver_components = {
        str(row["proposal_hash"]): {
            "accepted_proposal_ids": list(
                cast(Sequence[object], row["accepted_proposal_ids"] or [])
            ),
            "rejected_proposal_ids": list(
                cast(Sequence[object], row["rejected_proposal_ids"] or [])
            ),
            "rejected_rows": rejected_by_component.get(
                str(row["solver_component_id"]),
                [],
            ),
            "hard_clause_count": int(cast(int, row["hard_clause_count"])),
            "soft_clause_count": int(cast(int, row["soft_clause_count"])),
            "objective_cost": int(cast(int, row["objective_cost"])),
        }
        for row in solver_state_rows
        if row["solver_version"] == SOLVER_VERSION
        and row["constraint_version"] == CONSTRAINT_VERSION
    }
    return IncrementalPreparation(
        reusable_solver_components=reusable_solver_components,
        stats={
            "enabled": explicit_baseline is not None,
            "offline_state_replay": offline_replay_baseline is not None,
            "baseline_manifest_hash": _sha256(manifest_path),
            "markets_reused": (
                len(unchanged_market_ids) if parse_compatible else 0
            ),
            "markets_changed": (
                len(changed_market_ids)
                if parse_compatible
                else len(current_hashes)
            ),
            "markets_removed": len(removed_market_ids),
            "baseline_parse_entries_seeded": seeded,
            "baseline_embedding_vectors_available": (
                embedding_row_count if embedding_compatible else 0
            ),
            "baseline_solver_components_available": len(
                reusable_solver_components
            ),
            "invalidation_reasons": sorted(set(reasons)) or ["none"],
        },
        resources=IncrementalResources(
            prior_candidate_components=prior_candidate_components,
            prior_market_hashes=prior_markets,
            prior_solver_hashes=frozenset(reusable_solver_components),
            baseline_semantic_fingerprints=(
                semantic_neighbor_fingerprints
                if embedding_compatible
                and versions.get("retrieval") == RETRIEVAL_VERSION
                else []
            ),
            baseline_embedding_state_path=(
                embedding_state_path if embedding_compatible else None
            ),
            baseline_semantic_state_path=(
                semantic_neighbor_state_path
                if embedding_compatible
                and versions.get("retrieval") == RETRIEVAL_VERSION
                else None
            ),
            prior_classifications=(
                prior_classifications if classification_compatible else []
            ),
            prior_enabled_rules=frozenset(
                (manifest.get("rules") or {}).get("enabled") or []
            ),
            prior_propositions=prior_propositions,
            unchanged_market_ids=frozenset(unchanged_market_ids),
            reusable_candidates_path=(
                candidates_path if candidate_compatible else None
            ),
            baseline_candidate_blocks=(
                candidate_blocks_path
                if previous_limits.get("max_candidates")
                == config.max_candidates
                else None
            ),
            baseline_candidate_reasons=(
                candidate_reasons_path
                if previous_limits.get("max_candidates")
                == config.max_candidates
                else None
            ),
        ),
    )


def _parse_propositions(
    markets: Sequence[SourceMarket],
    source_schema: str,
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> ParsingStageResult:
    primary_entries = _parse_role_entries(
        markets, "primary", config, cache, state, inference
    )
    verifier_entries = _parse_role_entries(
        markets, "verifier", config, cache, state, inference
    )
    propositions: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    quarantines: list[dict[str, Any]] = []
    for market in markets:
        primary_entry = primary_entries[market.market_id]
        verifier_entry = verifier_entries[market.market_id]
        primary_market, primary_error = _validated_parse_entry(market, primary_entry)
        verifier_market, verifier_error = _validated_parse_entry(market, verifier_entry)
        consensus = merge_parsed_markets(market, primary_market, verifier_market)
        primary_by_outcome = {
            item.outcome: item for item in (primary_market.propositions if primary_market else [])
        }
        verifier_by_outcome = {
            item.outcome: item for item in (verifier_market.propositions if verifier_market else [])
        }
        primary_model = str(primary_entry.get("observed_model") or config.primary_model)
        verifier_model = str(verifier_entry.get("observed_model") or config.verifier_model)
        for source_outcome in market.outcomes:
            outcome_consensus = consensus[source_outcome.outcome]
            parsed = outcome_consensus.parsed
            primary_conflicts = _parse_authoritative_conflicts(
                market,
                source_outcome,
                primary_by_outcome.get(source_outcome.outcome),
            )
            verifier_conflicts = _parse_authoritative_conflicts(
                market,
                source_outcome,
                verifier_by_outcome.get(source_outcome.outcome),
            )
            proposition = _proposition_row(
                market,
                source_outcome,
                parsed,
                primary_model,
                verifier_model,
                source_schema,
                primary_error or verifier_error,
                inference.fingerprints["primary_parse"],
                inference.fingerprints["verifier_parse"],
                inference.fingerprints["consensus"],
                inference.profile.profile_id if inference.profile else None,
            )
            disagreements = list(proposition.pop("_authoritative_disagreements", []))
            reason_code = None
            explanation = None
            if primary_error or verifier_error:
                reason_code = "inference_failure"
                explanation = primary_error or verifier_error
            elif primary_conflicts or verifier_conflicts:
                reason_code = "authoritative_conflict"
                explanation = "; ".join(
                    [
                        *(f"primary: {value}" for value in primary_conflicts),
                        *(f"verifier: {value}" for value in verifier_conflicts),
                    ]
                )
            elif outcome_consensus.status != "agreed":
                reason_code = outcome_consensus.status
                explanation = ", ".join(outcome_consensus.disagreements)
            elif disagreements:
                reason_code = "authoritative_conflict"
                explanation = "; ".join(disagreements)
            elif float(proposition["parse_confidence"]) < config.parse_confidence:
                reason_code = "below_threshold"
                explanation = "Consensus parse confidence is below the threshold"
            if reason_code is not None:
                proposition["parse_status"] = "quarantined"
                quarantines.append(
                    quarantine_row(
                        proposition_a_id=str(proposition["proposition_id"]),
                        proposition_b_id=None,
                        stage="parse",
                        reason_code=reason_code,
                        proposed_relation=None,
                        confidence=float(proposition["parse_confidence"]),
                        primary_relation=None,
                        verifier_relation=None,
                        explanation=explanation or reason_code,
                        primary_model=primary_model,
                        verifier_model=verifier_model,
                        primary_fingerprint=inference.fingerprints["primary_parse"],
                        verifier_fingerprint=inference.fingerprints["verifier_parse"],
                        automation_profile_id=(inference.profile.profile_id if inference.profile else None),
                    )
                )
            propositions.append(proposition)
            assessments.extend(
                (
                    parse_assessment_row(
                        proposition_id=source_outcome.clob_token_id,
                        market_id=market.market_id,
                        role="primary",
                        model=primary_model,
                        fingerprint=inference.fingerprints["primary_parse"],
                        parsed=primary_by_outcome.get(source_outcome.outcome),
                        error=primary_error,
                        authoritative_conflicts=primary_conflicts,
                    ),
                    parse_assessment_row(
                        proposition_id=source_outcome.clob_token_id,
                        market_id=market.market_id,
                        role="verifier",
                        model=verifier_model,
                        fingerprint=inference.fingerprints["verifier_parse"],
                        parsed=verifier_by_outcome.get(source_outcome.outcome),
                        error=verifier_error,
                        authoritative_conflicts=verifier_conflicts,
                    ),
                )
            )
    return ParsingStageResult(
        propositions=sorted(
            propositions,
            key=lambda row: str(row["proposition_id"]),
        ),
        parse_assessments=sorted(assessments, key=lambda row: str(row["assessment_id"])),
        quarantines=quarantines,
    )


def _parse_authoritative_conflicts(
    market: SourceMarket,
    source: SourceOutcome,
    parsed: ParsedOutcome | None,
) -> list[str]:
    if parsed is None:
        return []
    conflicts: list[str] = []
    for field, authoritative in deterministic_extract(market, source).items():
        observed = getattr(parsed, field, None)
        if authoritative is None or observed is None:
            continue
        normalized_observed = (
            canonical_unit(str(observed)) if field == "unit" else observed
        )
        if normalized_observed != authoritative:
            conflicts.append(
                f"{field}={observed!r} conflicts with authoritative {authoritative!r}"
            )
    return conflicts


def _require_model_role_coverage(
    assessments: Sequence[dict[str, Any]],
    *,
    task: str,
) -> None:
    for role in ("primary", "verifier"):
        role_rows = [row for row in assessments if row.get("model_role") == role]
        if role_rows and not any(row.get("status") == "valid" for row in role_rows):
            raise RuntimeError(
                f"The {role} endpoint produced no valid {task} assessments"
            )


def _parse_role_entries(
    markets: Sequence[SourceMarket],
    role: Literal["primary", "verifier"],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> dict[str, dict[str, Any]]:
    task = f"{role}_parse"
    fingerprint = inference.fingerprints[task]
    model = config.primary_model if role == "primary" else config.verifier_model
    client = inference.primary_client if role == "primary" else inference.verifier_client
    requests = []
    for market in markets:
        payload = _market_payload(market)
        key = cache.key(
            task,
            fingerprint,
            PARSE_PROMPT_VERSION,
            _text_hash(_PARSE_PROMPT),
            _model_schema_hash(ParsedMarket),
            payload,
        )
        requests.append((market, key, payload))
    entries = cache.get_many([row[1] for row in requests], offline=config.offline)
    missing = [row for row in requests if row[1] not in entries]
    for _, key, _ in requests:
        entry = entries.get(key)
        if entry is not None:
            state.add_cached_usage(
                dict(entry.get("usage") or {}),
                _str_or_none(entry.get("usage_scope")),
                task,
            )
    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} {task} entries"
            )
        if client is None:
            raise RuntimeError(f"Online discovery requires the {role} endpoint")
        results = _run_async(
            _run_inference_stage(
                client,
                [row[2] for row in missing],
                lambda batch: _local_parse_market(client, batch[0], config, model=model),
                concurrency=config.llm_concurrency,
                close_after=False,
            )
        )
        by_market = {row[0].market_id: row for row in missing}
        pending: dict[str, dict[str, Any]] = {}
        for batch, result in results:
            payload = batch[0]
            market_id = str(payload["market_id"])
            _, key, _ = by_market[market_id]
            if isinstance(result, Exception):
                entry = cache_entry(
                    task=task,
                    parsed=None,
                    error=str(result),
                    observed_model=model,
                    usage={},
                    usage_scope=None,
                    state=("transient_failure" if _is_transient_error(result) else "stable_failure"),
                    error_type=type(result).__name__,
                    status_code=getattr(result, "status_code", None),
                )
            else:
                parsed, observed_model, usage = result
                entry = cache_entry(
                    task=task,
                    parsed=parsed.model_dump(mode="json"),
                    error=None,
                    observed_model=observed_model,
                    usage=usage,
                    usage_scope=cache.usage_scope(task, batch),
                    state="success",
                )
                state.add_usage(usage, task)
            pending[key] = entry
            entries[key] = entry
        cache.put_many(pending)
    result_by_market = {
        market.market_id: entries[key] for market, key, _ in requests
    }
    observed = {
        str(entry.get("observed_model") or model)
        for entry in result_by_market.values()
    }
    target = (
        state.observed_primary_parse_models
        if role == "primary"
        else state.observed_verifier_parse_models
    )
    target.update(observed)
    return result_by_market


def _validated_parse_entry(
    market: SourceMarket,
    entry: dict[str, Any],
) -> tuple[ParsedMarket | None, str | None]:
    error = cache_error(entry)
    if error or entry.get("parsed") is None:
        return None, error or "structured output omitted this market"
    try:
        parsed = ParsedMarket.model_validate(entry["parsed"])
        _validate_parsed_market(market, parsed)
        return parsed, None
    except (TypeError, ValueError) as exc:
        return None, str(exc)


def _market_payload(market: SourceMarket) -> dict[str, object]:
    return market_request(market).model_dump(mode="json")


async def _local_parse_market(
    client: StructuredClient,
    payload: dict[str, object],
    config: DiscoveryConfig,
    *,
    model: str,
) -> tuple[ParsedMarket, str, dict[str, int]]:
    result = await _with_retries(
        lambda: client.generate(
            model=model,
            system_prompt=_PARSE_PROMPT,
            payload=payload,
            response_model=ParsedMarket,
            settings=_generation_settings(config, role="parse"),
        )
    )
    parsed = ParsedMarket.model_validate(result.parsed)
    if parsed.market_id != str(payload["market_id"]):
        raise ValueError("Structured parse returned the wrong market_id")
    return (
        parsed,
        str(result.observed_model),
        dict(result.usage),
    )


def _hydrate_deterministic_candidates(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
) -> None:
    by_id = {str(row["proposition_id"]): row for row in propositions}
    for row in candidates:
        relation = _deterministic_relation(
            by_id[str(row["proposition_a_id"])],
            by_id[str(row["proposition_b_id"])],
            config.parse_confidence,
        )
        if relation is None or relation.get("rule_id") != row.get("rule_id"):
            raise RuntimeError(
                "Candidate workspace deterministic identity does not match current rules"
            )
        row["_deterministic"] = relation


def _automated_rule_gates() -> dict[str, Any]:
    return qualify_rule_registry(RULE_REGISTRY, _deterministic_relation)


def _embed_texts(texts: list[str], config: DiscoveryConfig) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise ImportError(
            "Automated discovery dependencies are missing; reinstall oddsfox-graph."
        ) from exc
    model = SentenceTransformer(
        config.embedding_model,
        revision=config.embedding_revision,
        local_files_only=True,
    )
    return model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def _derive_deterministic_edges(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    edges = []
    for candidate in candidates:
        relation = candidate.get("_deterministic")
        if not relation:
            continue
        edges.append(
            _logic_edge_row(
                relation,
                by_id,
                discovery_method="deterministic",
                rule_version=RULE_VERSION,
                prompt_version=None,
                assumptions=[],
            )
        )
    return edges


def _apply_nli_scores(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    scorer: NliScorer | None,
    *,
    injected_client: bool,
) -> None:
    if not candidates:
        return
    by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    fingerprint = inference.fingerprints["nli"]
    missing: list[tuple[dict[str, Any], str, dict[str, object]]] = []
    cached: dict[str, dict[str, Any]] = {}
    requests: list[tuple[dict[str, Any], str, dict[str, object]]] = []
    for candidate in candidates:
        a_id = str(candidate["proposition_a_id"])
        b_id = str(candidate["proposition_b_id"])
        payload: dict[str, object] = {
            "pair_id": _pair_id(a_id, b_id),
            "a": _nli_text(by_id[a_id]),
            "b": _nli_text(by_id[b_id]),
        }
        key = cache.key(
            "nli",
            fingerprint,
            NLI_INFERENCE_VERSION,
            fingerprint,
            fingerprint,
            payload,
        )
        requests.append((candidate, key, payload))
    entries = cache.get_many(
        [key for _, key, _ in requests],
        offline=config.offline,
    )
    for candidate, key, payload in requests:
        entry = entries.get(key)
        if entry is None:
            missing.append((candidate, key, payload))
        else:
            cached[str(payload["pair_id"])] = entry
    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} NLI entries"
            )
        if scorer is None and injected_client:
            parsed_rows = [
                {
                    "nli_a_to_b_entailment": 0.0,
                    "nli_a_to_b_contradiction": 0.0,
                    "nli_a_to_b_neutral": 1.0,
                    "nli_b_to_a_entailment": 0.0,
                    "nli_b_to_a_contradiction": 0.0,
                    "nli_b_to_a_neutral": 1.0,
                }
                for _ in missing
            ]
        else:
            effective_scorer = scorer or ModernBertNliScorer(
                config.nli_model,
                config.nli_revision,
            )
            directional = score_bidirectional(
                effective_scorer,
                [
                    (str(payload["a"]), str(payload["b"]))
                    for _, _, payload in missing
                ],
                batch_size=32,
            )
            parsed_rows = [
                scores_to_columns(scores) for scores in directional
            ]
        pending_cache_writes: dict[str, dict[str, Any]] = {}
        for (_, key, payload), parsed in zip(missing, parsed_rows, strict=True):
            entry = cache_entry(
                task="nli",
                parsed=parsed,
                error=None,
                observed_model=f"{config.nli_model}@{config.nli_revision}",
                usage={},
                usage_scope=None,
                state="success",
            )
            pending_cache_writes[key] = entry
            cached[str(payload["pair_id"])] = entry
        cache.put_many(pending_cache_writes)

    for candidate in candidates:
        a_id = str(candidate["proposition_a_id"])
        b_id = str(candidate["proposition_b_id"])
        pair_id = _pair_id(a_id, b_id)
        entry = cached.get(pair_id)
        if entry is None or not isinstance(entry.get("parsed"), dict):
            candidate["nli_action"] = "advisory_unavailable"
            continue
        columns = {
            key: float(value)
            for key, value in dict(entry["parsed"]).items()
        }
        candidate.update(columns)
        candidate["nli_action"] = "veto_only"


def _score_nli_candidate_batches(
    candidate_store: CandidateStore,
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    inference: _InferenceContext,
    scorer: NliScorer | None,
    *,
    injected_client: bool,
    limit: int,
) -> ConsensusStageResult:
    candidate_store.prepare_inference_queue(limit)
    effective_scorer = scorer
    if (
        effective_scorer is None
        and not injected_client
        and not config.offline
    ):
        effective_scorer = ModernBertNliScorer(
            config.nli_model,
            config.nli_revision,
        )
    for batch in candidate_store.inference_batches(batch_size=512):
        _apply_nli_scores(
            batch,
            propositions,
            config,
            cache,
            inference,
            effective_scorer,
            injected_client=injected_client,
        )
        candidate_store.update_nli_rows(batch)
    return ConsensusStageResult(edges=[], model_assessments=[], quarantines=[])


def _classify_candidate_batches(
    candidate_store: CandidateStore,
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    incremental_stats: IncrementalStats,
    incremental_resources: IncrementalResources,
    inference: _InferenceContext,
) -> ConsensusStageResult:
    candidate_store.prepare_inference_queue(config.max_llm_pairs)
    edges: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    quarantines: list[dict[str, Any]] = []
    for batch in candidate_store.inference_batches(batch_size=512):
        result = _classify_candidates(
            batch,
            propositions,
            config,
            cache,
            state,
            inference,
        )
        candidate_store.update_generative_rows(
            [
                row
                for row in batch
                if row.get("consensus_status") is not None
            ]
        )
        edges.extend(result.edges)
        assessments.extend(result.model_assessments)
        quarantines.extend(result.quarantines)
    return ConsensusStageResult(
        edges=edges,
        model_assessments=assessments,
        quarantines=quarantines,
    )


def _classify_candidates(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> ConsensusStageResult:
    by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    unresolved = sorted(
        (
            candidate
            for candidate in candidates
            if not candidate.get("_deterministic")
            and candidate.get("status") not in {"accepted", "rejected"}
        ),
        key=_candidate_sort_key,
    )
    selected = unresolved[: config.max_llm_pairs]
    selected_ids = {
        (str(row["proposition_a_id"]), str(row["proposition_b_id"]))
        for row in selected
    }
    for candidate in unresolved:
        pair = (
            str(candidate["proposition_a_id"]),
            str(candidate["proposition_b_id"]),
        )
        if pair not in selected_ids:
            candidate["status"] = "not_classified_budget"

    primary_entries = _classification_role_entries(
        selected, by_id, "primary", config, cache, state, inference
    )
    verifier_entries = _classification_role_entries(
        selected, by_id, "verifier", config, cache, state, inference
    )
    edges: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    quarantines: list[dict[str, Any]] = []
    for candidate in selected:
        a_id = str(candidate["proposition_a_id"])
        b_id = str(candidate["proposition_b_id"])
        pair_id = _pair_id(a_id, b_id)
        primary, primary_error, primary_model = _validated_classification_entry(
            primary_entries[pair_id], pair_id, config.primary_model
        )
        verifier, verifier_error, verifier_model = _validated_classification_entry(
            verifier_entries[pair_id], pair_id, config.verifier_model
        )
        primary_relation = (
            _derive_atomic_relation(primary)[0] if primary is not None else None
        )
        verifier_relation = (
            _derive_atomic_relation(verifier)[0] if verifier is not None else None
        )
        primary_assessment_error = primary_error or _model_assessment_error(
            primary,
            by_id[a_id],
            by_id[b_id],
        )
        verifier_assessment_error = verifier_error or _model_assessment_error(
            verifier,
            by_id[a_id],
            by_id[b_id],
        )
        primary_assessment_row = model_assessment_row(
            proposition_a_id=a_id,
            proposition_b_id=b_id,
            role="primary",
            model=primary_model,
            fingerprint=inference.fingerprints["primary_classify"],
            assessment=primary,
            validation_error=primary_assessment_error,
        )
        verifier_assessment_row = model_assessment_row(
            proposition_a_id=a_id,
            proposition_b_id=b_id,
            role="verifier",
            model=verifier_model,
            fingerprint=inference.fingerprints["verifier_classify"],
            assessment=verifier,
            validation_error=verifier_assessment_error,
        )
        assessments.extend((primary_assessment_row, verifier_assessment_row))
        consensus = relation_consensus(
            primary,
            verifier,
            by_id[a_id],
            by_id[b_id],
            nli_veto=nli_contradicts(primary_relation, candidate),
        )
        relation = consensus.relation
        confidence = consensus.confidence
        explanation = consensus.reason or (
            f"Independent primary and verifier assessments agree on {relation}."
        )
        candidate.update(
            {
                "classification_relation": relation,
                "classification_confidence": confidence,
                "primary_relation": primary_relation,
                "primary_confidence": primary.confidence if primary else None,
                "verifier_relation": verifier_relation,
                "verifier_confidence": verifier.confidence if verifier else None,
                "consensus_status": consensus.status,
                "a_implies_b": relation in {"equivalent", "A_implies_B"},
                "b_implies_a": relation in {"equivalent", "B_implies_A"},
                "explanation": explanation,
                "discovery_method": (
                    "generative_consensus" if consensus.status == "agreed" else None
                ),
                "prompt_version": CLASSIFY_PROMPT_VERSION,
                "primary_model_version": primary_model,
                "verifier_model_version": verifier_model,
                "primary_assessment_id": primary_assessment_row["assessment_id"],
                "verifier_assessment_id": verifier_assessment_row["assessment_id"],
                "primary_inference_fingerprint": inference.fingerprints[
                    "primary_classify"
                ],
                "verifier_inference_fingerprint": inference.fingerprints[
                    "verifier_classify"
                ],
                "consensus_fingerprint": inference.fingerprints["consensus"],
                "automation_profile_id": (
                    inference.profile.profile_id if inference.profile else None
                ),
            }
        )
        accepted_label = relation not in {None, "unrelated", "uncertain"}
        profile_relation = (
            "implies"
            if relation in {"A_implies_B", "B_implies_A"}
            else relation
        )
        qualified = (
            inference.profile is not None
            and profile_relation is not None
            and profile_relation in inference.profile.relations
            and inference.profile.relations[profile_relation].enabled
        )
        profile_threshold = (
            inference.profile.relations[profile_relation].threshold
            if qualified and inference.profile is not None and profile_relation
            else 1.0
        )
        effective_threshold = max(
            config.threshold_for(relation or "uncertain"),
            profile_threshold,
        )
        accepted = (
            accepted_label
            and consensus.status == "agreed"
            and confidence is not None
            and confidence >= effective_threshold
            and qualified
        )
        if accepted:
            assert inference.profile is not None
            assert primary is not None and verifier is not None and relation is not None
            assert confidence is not None
            candidate["status"] = "accepted"
            relation_row = _classification_relation(
                candidate,
                primary,
                relation,
                confidence,
            )
            edges.append(
                _logic_edge_row(
                    relation_row,
                    by_id,
                    discovery_method="generative_consensus",
                    rule_version=None,
                    prompt_version=CLASSIFY_PROMPT_VERSION,
                    assumptions=[],
                    primary_model_version=primary_model,
                    verifier_model_version=verifier_model,
                    primary_assessment_id=str(
                        primary_assessment_row["assessment_id"]
                    ),
                    verifier_assessment_id=str(
                        verifier_assessment_row["assessment_id"]
                    ),
                    primary_inference_fingerprint=inference.fingerprints[
                        "primary_classify"
                    ],
                    verifier_inference_fingerprint=inference.fingerprints[
                        "verifier_classify"
                    ],
                    consensus_fingerprint=inference.fingerprints["consensus"],
                    automation_profile_id=inference.profile.profile_id,
                )
            )
        elif relation == "unrelated" and consensus.status == "unrelated":
            candidate["status"] = "rejected"
        else:
            candidate["status"] = "quarantined"
            reason = consensus.status
            if consensus.status == "agreed" and not qualified:
                reason = "qualification_mismatch"
            elif consensus.status == "agreed" and (
                confidence is None or confidence < effective_threshold
            ):
                reason = "below_threshold"
            quarantines.append(
                quarantine_row(
                    proposition_a_id=a_id,
                    proposition_b_id=b_id,
                    stage="classification",
                    reason_code=reason,
                    proposed_relation=relation,
                    confidence=confidence,
                    primary_relation=primary_relation,
                    verifier_relation=verifier_relation,
                    explanation=explanation,
                    primary_model=primary_model,
                    verifier_model=verifier_model,
                    primary_fingerprint=inference.fingerprints[
                        "primary_classify"
                    ],
                    verifier_fingerprint=inference.fingerprints[
                        "verifier_classify"
                    ],
                    automation_profile_id=(
                        inference.profile.profile_id if inference.profile else None
                    ),
                )
            )
    return ConsensusStageResult(
        edges=edges,
        model_assessments=assessments,
        quarantines=quarantines,
    )


def _model_assessment_error(
    assessment: AtomicPairAssessment | None,
    proposition_a: dict[str, Any],
    proposition_b: dict[str, Any],
) -> str | None:
    if assessment is None:
        return "missing assessment"
    if assessment.unsupported_assumption:
        return "assessment declares an unsupported assumption"
    if assessment.assumptions:
        return "assessment contains assumptions"
    if assessment.requires_review:
        return "assessment requests quarantine"
    return _classification_validation_error(
        assessment,
        proposition_a,
        proposition_b,
    )


def _classification_role_entries(
    candidates: Sequence[dict[str, Any]],
    propositions: dict[str, dict[str, Any]],
    role: Literal["primary", "verifier"],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> dict[str, dict[str, Any]]:
    fingerprint = inference.fingerprints[f"{role}_classify"]
    model = config.primary_model if role == "primary" else config.verifier_model
    client = inference.primary_client if role == "primary" else inference.verifier_client
    task = f"{role}_classify"
    requests: list[tuple[str, str, dict[str, object]]] = []
    for candidate in candidates:
        payload = _pair_payload(candidate, propositions)
        key = cache.key(
            task,
            fingerprint,
            CLASSIFY_PROMPT_VERSION,
            _text_hash(_CLASSIFY_PROMPT),
            _model_schema_hash(AtomicPairAssessment),
            payload,
        )
        requests.append((str(payload["pair_id"]), key, payload))
    entries = cache.get_many([key for _, key, _ in requests], offline=config.offline)
    missing = [row for row in requests if row[1] not in entries]
    for _, key, _ in requests:
        entry = entries.get(key)
        if entry is not None:
            state.add_cached_usage(
                dict(entry.get("usage") or {}),
                _str_or_none(entry.get("usage_scope")),
                task,
            )
    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} {task} entries"
            )
        if client is None:
            raise RuntimeError(f"Online discovery requires the {role} endpoint")
        results = _run_async(
            _run_inference_stage(
                client,
                [row[2] for row in missing],
                lambda batch: _local_classify_pair(
                    client, batch[0], config, model=model
                ),
                concurrency=config.llm_concurrency,
                close_after=False,
            )
        )
        key_by_pair = {pair_id: key for pair_id, key, _ in missing}
        pending: dict[str, dict[str, Any]] = {}
        for payloads, result in results:
            payload = payloads[0]
            pair_id = str(payload["pair_id"])
            if isinstance(result, Exception):
                entry = cache_entry(
                    task=task,
                    parsed=None,
                    error=str(result),
                    observed_model=model,
                    usage={},
                    usage_scope=None,
                    state=(
                        "transient_failure"
                        if _is_transient_error(result)
                        else "stable_failure"
                    ),
                    error_type=type(result).__name__,
                    status_code=getattr(result, "status_code", None),
                )
            else:
                parsed, observed_model, usage = result
                entry = cache_entry(
                    task=task,
                    parsed=parsed.model_dump(mode="json"),
                    error=None,
                    observed_model=observed_model,
                    usage=usage,
                    usage_scope=cache.usage_scope(task, payloads),
                    state="success",
                )
                state.add_usage(usage, task)
                _observed_classify_models(state, role).add(observed_model)
            key = key_by_pair[pair_id]
            pending[key] = entry
            entries[key] = entry
        cache.put_many(pending)
    return {pair_id: entries[key] for pair_id, key, _ in requests}


def _observed_classify_models(
    state: RunState, role: Literal["primary", "verifier"]
) -> set[str]:
    return (
        state.observed_primary_classify_models
        if role == "primary"
        else state.observed_verifier_classify_models
    )


def _validated_classification_entry(
    entry: dict[str, Any], pair_id: str, default_model: str
) -> tuple[AtomicPairAssessment | None, str | None, str]:
    model = str(entry.get("observed_model") or default_model)
    error = cache_error(entry)
    if error or entry.get("parsed") is None:
        return None, error or "missing classification", model
    try:
        parsed = AtomicPairAssessment.model_validate(entry["parsed"])
        if parsed.pair_id != pair_id:
            raise ValueError("classification returned the wrong pair_id")
        return parsed, None, model
    except (TypeError, ValueError) as exc:
        return None, str(exc), model


def _atomic_explanation(
    assessment: AtomicPairAssessment,
    relation: str | None,
    error: str | None,
) -> str:
    if error:
        return error
    return (
        f"Atomic judgments derive {relation}: A→B={assessment.a_implies_b}, "
        f"B→A={assessment.b_implies_a}, both_true={assessment.can_both_be_true}, "
        f"one_required={assessment.must_one_be_true}, "
        f"related={assessment.logically_related}."
    )


async def _local_classify_pair(
    client: StructuredClient,
    payload: dict[str, object],
    config: DiscoveryConfig,
    *,
    model: str,
) -> tuple[AtomicPairAssessment, str, dict[str, int]]:
    result = await _with_retries(
        lambda: client.generate(
            model=model,
            system_prompt=_CLASSIFY_PROMPT,
            payload=payload,
            response_model=AtomicPairAssessment,
            settings=_generation_settings(config, role="classify"),
        )
    )
    parsed = AtomicPairAssessment.model_validate(result.parsed)
    if parsed.pair_id != str(payload["pair_id"]):
        raise ValueError("Structured classification returned the wrong pair_id")
    return (
        parsed,
        str(result.observed_model),
        dict(result.usage),
    )


def _pair_payload(
    candidate: dict[str, Any],
    propositions: dict[str, dict[str, Any]],
) -> dict[str, object]:
    a_id = str(candidate["proposition_a_id"])
    b_id = str(candidate["proposition_b_id"])
    return pair_request(
        propositions[a_id],
        propositions[b_id],
    ).model_dump(mode="json")


def _classification_relation(
    candidate: dict[str, Any],
    classification: AtomicPairAssessment,
    relation: str,
    consensus_confidence: float,
) -> dict[str, Any]:
    a_id = str(candidate["proposition_a_id"])
    b_id = str(candidate["proposition_b_id"])
    if relation == "A_implies_B":
        edge_type, src, dst = "implies", a_id, b_id
    elif relation == "B_implies_A":
        edge_type, src, dst = "implies", b_id, a_id
    else:
        edge_type = relation
        src, dst = sorted((a_id, b_id))
    return {
        "edge_type": edge_type,
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_basis": "dual_model_consensus",
        "explanation": _atomic_explanation(classification, relation, None),
        "confidence": consensus_confidence,
    }


def _logic_edge_row(
    relation: dict[str, Any],
    propositions: dict[str, dict[str, Any]],
    *,
    discovery_method: str,
    rule_version: str | None,
    prompt_version: str | None,
    assumptions: list[str],
    primary_model_version: str | None = None,
    verifier_model_version: str | None = None,
    primary_assessment_id: str | None = None,
    verifier_assessment_id: str | None = None,
    primary_inference_fingerprint: str | None = None,
    verifier_inference_fingerprint: str | None = None,
    consensus_fingerprint: str | None = None,
    automation_profile_id: str | None = None,
) -> dict[str, Any]:
    src = propositions[str(relation["src_node_id"])]
    dst = propositions[str(relation["dst_node_id"])]
    explanation = str(relation["explanation"])
    rule_id = relation.get("rule_id") if discovery_method == "deterministic" else None
    proposal_id = _text_hash(
        "|".join(
            (
                str(relation["src_node_id"]),
                str(relation["dst_node_id"]),
                str(relation["edge_type"]),
                discovery_method,
                str(rule_id or ""),
                str(primary_model_version or ""),
                str(verifier_model_version or ""),
                str(prompt_version or ""),
            )
        )
    )
    return {
        "src_node_id": relation["src_node_id"],
        "dst_node_id": relation["dst_node_id"],
        "edge_type": relation["edge_type"],
        "edge_basis": relation["edge_basis"],
        "confidence": relation["confidence"],
        "market_id_src": src["market_id"],
        "market_id_dst": dst["market_id"],
        "event_slug_src": src.get("event_slug") or src.get("event_id") or "",
        "event_slug_dst": dst.get("event_slug") or dst.get("event_id") or "",
        "evidence": explanation,
        "discovery_method": discovery_method,
        "rule_version": rule_version,
        "primary_model_version": primary_model_version,
        "verifier_model_version": verifier_model_version,
        "primary_assessment_id": primary_assessment_id,
        "verifier_assessment_id": verifier_assessment_id,
        "prompt_version": prompt_version,
        "explanation": explanation,
        "assumptions": assumptions,
        "rule_id": rule_id,
        "proposal_id": proposal_id,
        "solver_version": None,
        "constraint_version": None,
        "solver_component_id": None,
        "primary_inference_fingerprint": primary_inference_fingerprint,
        "verifier_inference_fingerprint": verifier_inference_fingerprint,
        "consensus_fingerprint": consensus_fingerprint,
        "automation_profile_id": automation_profile_id,
    }


def _solve_logic_edges(
    edges: Sequence[dict[str, Any]],
    *,
    reusable_solver_components: dict[str, dict[str, Any]] | None = None,
) -> SolvingStageResult:
    accepted, rejected, stats = solve_proposals(
        edges,
        reusable_components=reusable_solver_components,
    )
    return SolvingStageResult(
        accepted_edges=accepted,
        rejected_edges=rejected,
        quarantines=[],
        stats=stats,
    )


def _solver_component_state_rows(
    accepted: Sequence[dict[str, Any]],
    rejected: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    accepted_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        accepted_by_component[str(row["solver_component_id"])].append(row)
    for row in rejected:
        rejected_by_component[str(row["solver_component_id"])].append(row)
    rows = []
    for component_id in sorted(
        set(accepted_by_component) | set(rejected_by_component)
    ):
        accepted_rows = accepted_by_component[component_id]
        rejected_rows = rejected_by_component[component_id]
        proposals = [*accepted_rows, *rejected_rows]
        representative = proposals[0]
        rows.append(
            {
                "solver_component_id": component_id,
                "proposal_hash": proposal_set_hash(proposals),
                "accepted_proposal_ids": sorted(
                    str(row["proposal_id"]) for row in accepted_rows
                ),
                "rejected_proposal_ids": sorted(
                    str(row["proposal_id"]) for row in rejected_rows
                ),
                "proposal_count": len(proposals),
                "hard_clause_count": int(
                    representative["_solver_component_hard_clauses"]
                ),
                "soft_clause_count": int(
                    representative["_solver_component_soft_clauses"]
                ),
                "objective_cost": int(
                    representative["_solver_component_objective"]
                ),
                "solver_version": SOLVER_VERSION,
                "constraint_version": CONSTRAINT_VERSION,
            }
        )
    return rows


def _proposition_fingerprint_rows(
    propositions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    ignored = {
        "parse_confidence",
        "parse_status",
        "primary_parser_model",
        "verifier_parser_model",
        "prompt_version",
        "primary_parse_fingerprint",
        "verifier_parse_fingerprint",
        "consensus_fingerprint",
        "automation_profile_id",
        "source_schema",
    }
    return [
        {
            "proposition_id": str(row["proposition_id"]),
            "market_id": str(row["market_id"]),
            "market_source_hash": str(row["market_source_hash"]),
            "parse_fingerprint": _text_hash(
                json.dumps(
                    {
                        key: row.get(key)
                        for key in sorted(PROPOSITION_COLUMNS)
                        if key not in ignored
                    },
                    default=str,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            "normalization_version": NORMALIZATION_VERSION,
        }
        for row in propositions
    ]


def _record_candidate_component_reuse(
    incremental_stats: IncrementalStats,
    candidate_components: Sequence[dict[str, Any]],
    prior: dict[str, str],
) -> None:
    reused = sum(
        prior.get(str(row["component_id"])) == row["component_fingerprint"]
        for row in candidate_components
    )
    incremental_stats["candidate_components_reused"] = reused
    incremental_stats["candidate_components_recomputed"] = (
        len(candidate_components) - reused
    )
    incremental_stats["candidate_components_removed"] = len(
        set(prior)
        - {str(row["component_id"]) for row in candidate_components}
    )


def _record_semantic_neighborhood_reuse(
    incremental_stats: IncrementalStats,
    prior_rows: Sequence[dict[str, Any]],
    current_rows: Sequence[dict[str, Any]],
    execution_plan: ExecutionPlan | None = None,
    execution_rows: Sequence[dict[str, Any]] | None = None,
) -> None:
    def fingerprints(
        rows: Sequence[dict[str, Any]],
    ) -> dict[str, str]:
        if rows and "neighborhood_fingerprint" in rows[0]:
            return {
                str(row["proposition_id"]): str(
                    row["neighborhood_fingerprint"]
                )
                for row in rows
            }
        grouped: dict[str, list[tuple[str, float, int]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["proposition_id"])].append(
                (
                    str(row["neighbor_id"]),
                    float(row["similarity"]),
                    int(row["neighbor_rank"]),
                )
            )
        return {
            proposition_id: _text_hash(
                "|".join(
                    f"{neighbor_id}:{similarity}:{rank}"
                    for neighbor_id, similarity, rank in sorted(neighbors)
                )
            )
            for proposition_id, neighbors in grouped.items()
        }

    prior = fingerprints(prior_rows)
    current = fingerprints(current_rows)
    execution_status = {
        str(row["proposition_id"]): str(row["status"])
        for row in execution_rows or []
    }
    reused = sum(
        execution_status.get(proposition_id) == "reused"
        and prior.get(proposition_id) == fingerprint
        for proposition_id, fingerprint in current.items()
    )
    incremental_stats["semantic_neighborhoods_reused"] = reused
    incremental_stats["semantic_neighborhoods_recomputed"] = (
        len(current) - reused
    )
    incremental_stats["semantic_neighborhoods_removed"] = len(
        set(prior) - set(current)
    )
    if execution_plan is None:
        return
    for proposition_id, fingerprint in sorted(current.items()):
        prior_fingerprint = prior.get(proposition_id)
        reused = (
            execution_status.get(proposition_id) == "reused"
            and prior_fingerprint == fingerprint
        )
        execution_plan.add(
            stage="semantic_neighborhoods",
            unit_type="proposition",
            unit_id=proposition_id,
            status="reused" if reused else "recomputed",
            invalidation_reasons=[] if reused else ["embedding_or_neighbor_set"],
            input_fingerprint=prior_fingerprint,
            output_fingerprint=fingerprint,
        )
    for proposition_id in sorted(set(prior) - set(current)):
        execution_plan.add(
            stage="semantic_neighborhoods",
            unit_type="proposition",
            unit_id=proposition_id,
            status="removed",
            invalidation_reasons=["proposition_removed"],
            input_fingerprint=prior[proposition_id],
        )


def _stage_workspace_tables(
    candidates: CandidateStore,
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
    logic_edges: Sequence[dict[str, Any]],
    rejected_edges: Sequence[dict[str, Any]],
    parse_errors: Sequence[dict[str, Any]],
    parse_assessments: Sequence[dict[str, Any]],
    model_assessments: Sequence[dict[str, Any]],
    quarantines: Sequence[dict[str, Any]],
    qualification_cases: Sequence[dict[str, Any]],
    proposition_fingerprint_state: Sequence[dict[str, Any]],
    candidate_component_state: Sequence[dict[str, Any]],
    solver_component_state: Sequence[dict[str, Any]],
    execution_plan_rows: Sequence[dict[str, Any]],
    inference: _InferenceContext,
) -> None:
    db = candidates.db
    parser_by_market = {
        str(row["market_id"]): str(row["primary_parser_model"])
        for row in propositions
    }
    _create_and_fill(
        db,
        "propositions_v",
        PROPOSITION_COLUMNS,
        [_public_proposition(row) for row in propositions],
    )
    node_rows = _node_rows(markets, propositions)
    _create_and_fill(db, "nodes_table", NODE_COLUMNS, node_rows)
    db.execute(
        """
        CREATE VIEW nodes_v AS
        SELECT
            n.*,
            p.subject[1] AS stage_subject,
            CASE
                WHEN lower(coalesce(p.object, p.predicate, '')) LIKE '%final%'
                    THEN lower(coalesce(p.object, p.predicate))
                ELSE NULL
            END AS stage_key
        FROM nodes_table n
        JOIN propositions_v p ON p.proposition_id = n.node_id
        """
    )
    _create_and_fill(
        db,
        "market_groups_v",
        MARKET_GROUP_COLUMNS,
        _market_group_rows(markets, node_rows),
    )
    _create_and_fill(db, "logic_edges_v", LOGIC_EDGE_COLUMNS, logic_edges)
    _create_and_fill(
        db,
        "rejected_edges_v",
        REJECTED_EDGE_COLUMNS,
        rejected_edges,
    )
    _create_and_fill(
        db,
        "parse_errors_v",
        PARSE_ERROR_COLUMNS,
        parse_errors,
    )
    _create_and_fill(
        db,
        "parse_assessments_v",
        PARSE_ASSESSMENT_COLUMNS,
        parse_assessments,
    )
    _create_and_fill(
        db,
        "model_assessments_v",
        MODEL_ASSESSMENT_COLUMNS,
        model_assessments,
    )
    db.execute(
        """
        INSERT INTO model_assessments_v
        SELECT
            sha256(
                c.proposition_a_id || '|' || c.proposition_b_id || '|' ||
                role.model_role || '|not_assessed'
            ) AS assessment_id,
            sha256(c.proposition_a_id || '|' || c.proposition_b_id) AS pair_id,
            c.proposition_a_id,
            c.proposition_b_id,
            role.model_role,
            role.model_version,
            role.inference_fingerprint,
            NULL::VARCHAR AS relation,
            0.0::DOUBLE AS confidence,
            NULL::VARCHAR AS atomic_a_implies_b,
            NULL::VARCHAR AS atomic_b_implies_a,
            NULL::VARCHAR AS atomic_can_both_be_true,
            NULL::VARCHAR AS atomic_must_one_be_true,
            NULL::VARCHAR AS atomic_logically_related,
            NULL::VARCHAR AS supporting_fields_json,
            []::VARCHAR[] AS assumptions,
            false AS unsupported_assumption,
            c.deterministic_relation IS NULL AS requires_review,
            CASE
                WHEN c.deterministic_relation IS NOT NULL THEN 'not_required'
                ELSE 'not_selected'
            END AS status,
            CASE
                WHEN c.deterministic_relation IS NOT NULL
                    THEN 'deterministic proposal did not require model inference'
                ELSE coalesce(c.status, 'not selected for bounded inference')
            END AS validation_error
        FROM relation_candidates_work c
        CROSS JOIN (
            VALUES
                ('primary', ?::VARCHAR, ?::VARCHAR),
                ('verifier', ?::VARCHAR, ?::VARCHAR)
        ) role(model_role, model_version, inference_fingerprint)
        WHERE NOT EXISTS (
            SELECT 1
            FROM model_assessments_v assessment
            WHERE assessment.proposition_a_id = c.proposition_a_id
              AND assessment.proposition_b_id = c.proposition_b_id
              AND assessment.model_role = role.model_role
        )
        """,
        [
            inference.primary_manifest.loaded_model_identifier,
            inference.fingerprints["primary_classify"],
            inference.verifier_manifest.loaded_model_identifier,
            inference.fingerprints["verifier_classify"],
        ],
    )
    _create_and_fill(
        db,
        "quarantined_pairs_v",
        QUARANTINE_COLUMNS,
        quarantines,
    )
    _create_and_fill(
        db,
        "qualification_cases_v",
        QUALIFICATION_CASE_COLUMNS,
        qualification_cases,
    )
    _create_and_fill(
        db,
        "market_state_v",
        MARKET_STATE_COLUMNS,
        [
            {
                "market_id": market.market_id,
                "source_hash": market.source_hash,
                "parse_model": parser_by_market.get(market.market_id, ""),
                "parse_prompt_version": PARSE_PROMPT_VERSION,
                "normalization_version": NORMALIZATION_VERSION,
                "rule_version": RULE_VERSION,
            }
            for market in markets
        ],
    )
    _create_and_fill(
        db,
        "proposition_fingerprints_v",
        PROPOSITION_FINGERPRINT_COLUMNS,
        proposition_fingerprint_state,
    )
    _create_and_fill(
        db,
        "candidate_components_v",
        CANDIDATE_COMPONENT_STATE_COLUMNS,
        candidate_component_state,
    )
    _create_and_fill(
        db,
        "solver_components_v",
        SOLVER_COMPONENT_STATE_COLUMNS,
        solver_component_state,
    )
    _create_and_fill(
        db,
        "execution_plan_v",
        EXECUTION_PLAN_COLUMNS,
        execution_plan_rows,
    )


def _write_discovery_artifacts(
    directory: Path,
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
    candidates: CandidateStore,
    logic_edges: Sequence[dict[str, Any]],
    rejected_edges: Sequence[dict[str, Any]],
    quarantines: Sequence[dict[str, Any]],
    *,
    source_schema: str,
    input_rows: int,
    input_selection: dict[str, object],
    solver_stats: dict[str, int],
    rule_support: dict[str, Any],
    candidate_member_limit: int,
    incremental_stats: dict[str, Any],
    config: DiscoveryConfig,
    inference: _InferenceContext,
) -> PublicationStageResult:
    publication_started = time.perf_counter()
    publication_checkpoint = publication_started
    publication_stage_timings: dict[str, float] = {}

    def record_publication_stage(name: str) -> None:
        nonlocal publication_checkpoint
        now = time.perf_counter()
        publication_stage_timings[name] = round(
            now - publication_checkpoint,
            3,
        )
        publication_checkpoint = now

    database_path = directory / "oddsfox_graph.duckdb"
    candidates.promote_to(database_path)
    db = DuckDB(database_path)
    try:
        db.execute("SET TimeZone = 'UTC'")
        db.execute("SET memory_limit = '192MB'")
        db.execute("SET preserve_insertion_order = false")
        db.execute("SET threads = 2")
        db.execute(
            f"SET temp_directory = '{q(directory / '.duckdb-spill')}'"
        )
        CandidateStore.promote_public_tables(db)
        record_publication_stage("promote_workspace")

        _copy_table(
            db,
            "nodes_table",
            directory / "nodes.parquet",
            list(NODE_COLUMNS),
            "node_id",
        )
        _copy_table(
            db,
            "market_groups_v",
            directory / "market_groups.parquet",
            list(MARKET_GROUP_COLUMNS),
            "market_id",
        )
        _copy_table(
            db,
            "propositions_v",
            directory / "propositions.parquet",
            list(PROPOSITION_COLUMNS),
            "proposition_id",
        )
        _copy_table(
            db,
            "relation_candidates_v",
            directory / "relation_candidates.parquet",
            list(CANDIDATE_COLUMNS),
            "proposition_a_id, proposition_b_id",
        )
        _copy_table(
            db,
            "logic_edges_v",
            directory / "logic_edges.parquet",
            ARTIFACT_COLUMNS["logic_edges.parquet"],
            "src_node_id, dst_node_id, edge_type",
        )
        _copy_table(
            db,
            "rejected_edges_v",
            directory / "rejected_edges.parquet",
            list(REJECTED_EDGE_COLUMNS),
            "proposal_id",
        )
        _copy_table(
            db,
            "parse_errors_v",
            directory / "parse_errors.parquet",
            list(PARSE_ERROR_COLUMNS),
            "error_id",
        )
        _copy_table(
            db,
            "parse_assessments_v",
            directory / "parse_assessments.parquet",
            list(PARSE_ASSESSMENT_COLUMNS),
            "assessment_id",
        )
        _copy_table(
            db,
            "model_assessments_v",
            directory / "model_assessments.parquet",
            list(MODEL_ASSESSMENT_COLUMNS),
            "assessment_id",
        )
        _copy_table(
            db,
            "quarantined_pairs_v",
            directory / "quarantined_pairs.parquet",
            list(QUARANTINE_COLUMNS),
            "quarantine_id",
        )
        _copy_table(
            db,
            "qualification_cases_v",
            directory / "qualification_cases.parquet",
            list(QUALIFICATION_CASE_COLUMNS),
            "case_id",
        )
        state_directory = directory / "state"
        state_directory.mkdir(parents=True, exist_ok=True)
        _copy_table(
            db,
            "market_state_v",
            state_directory / "market_state.parquet",
            list(MARKET_STATE_COLUMNS),
            "market_id",
        )
        _copy_table(
            db,
            "proposition_fingerprints_v",
            state_directory / "proposition_fingerprints.parquet",
            list(PROPOSITION_FINGERPRINT_COLUMNS),
            "proposition_id",
        )
        _copy_table(
            db,
            "proposition_embeddings_v",
            state_directory / "proposition_embeddings.parquet",
            list(EMBEDDING_STATE_COLUMNS),
            "proposition_id",
        )
        _copy_table(
            db,
            "semantic_neighbors_v",
            state_directory / "semantic_neighbors.parquet",
            list(SEMANTIC_NEIGHBOR_STATE_COLUMNS),
            "proposition_id, neighbor_rank",
        )
        _copy_table(
            db,
            "candidate_components_v",
            state_directory / "candidate_components.parquet",
            list(CANDIDATE_COMPONENT_STATE_COLUMNS),
            "component_id",
        )
        _copy_table(
            db,
            "candidate_blocks_v",
            state_directory / "candidate_blocks.parquet",
            list(CANDIDATE_BLOCK_COLUMNS),
            "block_id",
        )
        _copy_table(
            db,
            "candidate_reason_rows_v",
            state_directory / "candidate_reason_rows.parquet",
            list(CANDIDATE_REASON_COLUMNS),
            "block_id, proposition_a_id, proposition_b_id",
        )
        _copy_table(
            db,
            "solver_components_v",
            state_directory / "solver_components.parquet",
            list(SOLVER_COMPONENT_STATE_COLUMNS),
            "solver_component_id",
        )
        _copy_table(
            db,
            "execution_plan_v",
            state_directory / "execution_plan.parquet",
            list(EXECUTION_PLAN_COLUMNS),
            "stage, unit_type, unit_id",
        )
        record_publication_stage("export_parquet")
        write_conditionals(db, directory)
        write_graph_snapshot(db, directory)
        record_publication_stage("derived_artifacts")
        candidate_stats = {
            key: int(
                db.scalar(
                    f"""
                    SELECT count(*)
                    FROM relation_candidates_v
                    {where}
                    """
                )
                or 0
            )
            for key, where in {
                "candidate_edges": "",
                "classified_pairs": (
                    "WHERE discovery_method = 'generative_consensus'"
                ),
                "unclassified_budget_pairs": (
                    "WHERE status = 'not_classified_budget'"
                ),
            }.items()
        }
        stats: dict[str, object] = {
            "input_rows": input_rows,
            "input_schema": source_schema,
            "input_selection": input_selection,
            "markets": len(markets),
            "tokens": len(propositions),
            "active_markets": sum(1 for market in markets if market.is_active),
            "closed_markets": sum(1 for market in markets if market.is_closed),
            **candidate_stats,
            "logic_edges": len(logic_edges),
            "deterministic_logic_edges": sum(
                1
                for row in logic_edges
                if row.get("discovery_method") == "deterministic"
            ),
            "model_logic_edges": sum(
                1
                for row in logic_edges
                if row.get("discovery_method")
                == "generative_consensus"
            ),
            "conditional_edges": int(
                db.scalar("SELECT count(*) FROM conditional_edges_v") or 0
            ),
            "quarantined_pairs": len(quarantines),
            "rejected_edges": len(rejected_edges),
            "parse_failures": sum(
                1 for row in propositions if row["parse_status"] != "parsed"
            ),
            "solver": solver_stats,
            "rules": rule_support,
            "candidate_workspace": {
                "structured_member_limit": candidate_member_limit,
                **candidates.instrumentation(),
                "structured_blocks": int(
                    db.scalar("SELECT count(*) FROM candidate_blocks_v") or 0
                ),
                "truncated_structured_blocks": int(
                    db.scalar(
                        """
                        SELECT count(*)
                        FROM candidate_blocks_v
                        WHERE member_count > ?
                        """,
                        [candidate_member_limit],
                    )
                    or 0
                ),
                "persisted_reason_contributions": int(
                    db.scalar(
                        "SELECT count(*) FROM candidate_reason_rows_v"
                    )
                    or 0
                ),
                "state_rows": {
                    "embeddings": int(
                        db.scalar(
                            "SELECT count(*) FROM proposition_embeddings_v"
                        )
                        or 0
                    ),
                    "semantic_neighbors": int(
                        db.scalar(
                            "SELECT count(*) FROM semantic_neighbors_v"
                        )
                        or 0
                    ),
                    "candidate_components": int(
                        db.scalar(
                            "SELECT count(*) FROM candidate_components_v"
                        )
                        or 0
                    ),
                    "execution_plan": int(
                        db.scalar("SELECT count(*) FROM execution_plan_v")
                        or 0
                    ),
                },
            },
            "incremental": {
                **incremental_stats,
                "embedding_vectors_reused": (
                    candidates.embedding_vectors_reused
                ),
                "embedding_vectors_recomputed": (
                    candidates.embedding_vectors_recomputed
                ),
            },
            "time_range_start": min(
                (
                    timestamp
                    for market in markets
                    if (
                        timestamp := market.first_seen_ts or market.time_start
                    )
                    is not None
                ),
                default=None,
            ),
            "time_range_end": max(
                (
                    timestamp
                    for market in markets
                    if (
                        timestamp := market.last_seen_ts or market.time_end
                    )
                    is not None
                ),
                default=None,
            ),
        }
        _validate_discovery_artifacts(db, directory)
        db.execute("CHECKPOINT")
        record_publication_stage("validate_and_checkpoint")
        candidate_workspace = stats["candidate_workspace"]
        assert isinstance(candidate_workspace, dict)
        candidate_workspace["database_bytes"] = database_path.stat().st_size
        spill_directory = directory / ".duckdb-spill"
        candidate_workspace["spill_bytes"] = sum(
            path.stat().st_size
            for path in spill_directory.rglob("*")
            if path.is_file()
        ) if spill_directory.is_dir() else 0
        write_reports(db, directory, stats)
        _write_json_atomic(
            directory / "primary_model_manifest.json",
            inference.primary_manifest.model_dump(mode="json"),
        )
        _write_json_atomic(
            directory / "verifier_model_manifest.json",
            inference.verifier_manifest.model_dump(mode="json"),
        )
        if inference.profile is None or inference.qualification_report is None:
            raise RuntimeError("Publication requires an automated qualification profile")
        _write_json_atomic(
            directory / "automation_profile.json",
            inference.profile.model_dump(mode="json"),
        )
        _write_json_atomic(
            directory / "qualification_report.json",
            inference.qualification_report,
        )
        if config.compute_profile is not None:
            _write_json_atomic(
                directory / "compute_profile.json",
                load_compute_profile(config.compute_profile).model_dump(mode="json"),
            )
        record_publication_stage("reports_and_provenance")
        stats["publication_stage_timings"] = publication_stage_timings
        stats["publication_bytes"] = sum(
            path.stat().st_size
            for path in directory.rglob("*")
            if path.is_file() and ".duckdb-spill" not in path.parts
        )
        return PublicationStageResult(stats=stats)
    finally:
        db.close()


def _node_rows(
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    market_by_id = {market.market_id: market for market in markets}
    rows = []
    for proposition in propositions:
        market = market_by_id[str(proposition["market_id"])]
        outcome = str(proposition["outcome"])
        canonical = (
            proposition["question"]
            if outcome == "Yes"
            else (
                f"NOT({proposition['question']})"
                if outcome == "No"
                else f"{proposition['question']} :: {outcome}"
            )
        )
        stage_rank = _stage_rank(proposition)
        market_family = (
            "single_winner"
            if _is_winner_proposition(proposition)
            else ("stage_progression" if stage_rank is not None else "unknown")
        )
        rows.append(
            {
                "node_id": proposition["proposition_id"],
                "market_id": proposition["market_id"],
                "outcome_index": proposition["outcome_index"],
                "clob_token_id": proposition["clob_token_id"],
                "question": proposition["question"],
                "outcome_label": outcome,
                "event_slug": market.event_slug or market.event_id or "",
                "is_active": market.is_active,
                "is_closed": market.is_closed,
                "market_family": market_family,
                "canonical_proposition": canonical,
                "proposition_type": (
                    "binary" if outcome in {"Yes", "No"} else "named_outcome"
                ),
                "expected_tokens": len(market.outcomes),
                "first_seen_ts": market.first_seen_ts or market.time_start,
                "last_seen_ts": market.last_seen_ts or market.time_end,
            }
        )
    return sorted(rows, key=lambda row: str(row["node_id"]))


def _market_group_rows(
    markets: Sequence[SourceMarket],
    node_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in node_rows:
        by_market[str(row["market_id"])].append(row)
    rows = []
    for market in sorted(markets, key=lambda item: item.market_id):
        nodes = sorted(
            by_market[market.market_id],
            key=lambda row: int(row["outcome_index"]),
        )
        rows.append(
            {
                "market_id": market.market_id,
                "event_slug": market.event_slug or market.event_id or "",
                "question": market.question,
                "market_family": next(
                    (
                        str(row["market_family"])
                        for row in nodes
                        if row["market_family"] != "unknown"
                    ),
                    "unknown",
                ),
                "num_tokens": len(nodes),
                "token_ids": [str(row["node_id"]) for row in nodes],
                "outcome_labels": [str(row["outcome_label"]) for row in nodes],
                "is_active": market.is_active,
                "is_closed": market.is_closed,
                "first_seen_ts": market.first_seen_ts or market.time_start,
                "last_seen_ts": market.last_seen_ts or market.time_end,
            }
        )
    return rows


def _validate_discovery_artifacts(db: DuckDB, directory: Path) -> None:
    contracts = {
        "nodes.parquet": NODE_COLUMNS,
        "market_groups.parquet": MARKET_GROUP_COLUMNS,
        "propositions.parquet": PROPOSITION_COLUMNS,
        "relation_candidates.parquet": CANDIDATE_COLUMNS,
        "logic_edges.parquet": LOGIC_EDGE_COLUMNS,
        "rejected_edges.parquet": REJECTED_EDGE_COLUMNS,
        "parse_errors.parquet": PARSE_ERROR_COLUMNS,
        "conditional_edges.parquet": {
            name: ""
            for name in ARTIFACT_COLUMNS["conditional_edges.parquet"]
        },
        "parse_assessments.parquet": PARSE_ASSESSMENT_COLUMNS,
        "model_assessments.parquet": MODEL_ASSESSMENT_COLUMNS,
        "quarantined_pairs.parquet": QUARANTINE_COLUMNS,
        "qualification_cases.parquet": QUALIFICATION_CASE_COLUMNS,
        "state/market_state.parquet": MARKET_STATE_COLUMNS,
        "state/proposition_fingerprints.parquet": PROPOSITION_FINGERPRINT_COLUMNS,
        "state/proposition_embeddings.parquet": EMBEDDING_STATE_COLUMNS,
        "state/semantic_neighbors.parquet": SEMANTIC_NEIGHBOR_STATE_COLUMNS,
        "state/candidate_components.parquet": CANDIDATE_COMPONENT_STATE_COLUMNS,
        "state/candidate_blocks.parquet": CANDIDATE_BLOCK_COLUMNS,
        "state/candidate_reason_rows.parquet": CANDIDATE_REASON_COLUMNS,
        "state/solver_components.parquet": SOLVER_COMPONENT_STATE_COLUMNS,
        "state/execution_plan.parquet": EXECUTION_PLAN_COLUMNS,
    }
    for artifact, expected in contracts.items():
        path = directory / artifact
        if not path.is_file():
            raise RuntimeError(f"Missing discovery artifact {artifact}")
        actual = [
            str(row["column_name"])
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(path)}')"
            )
        ]
        if actual != list(expected):
            raise RuntimeError(
                f"{artifact} schema drift: expected {list(expected)}, got {actual}"
            )
    duplicate_edges = int(
        db.scalar(
            """
            SELECT count(*)
            FROM (
                SELECT src_node_id, dst_node_id, edge_type
                FROM logic_edges_v
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            )
            """
        )
        or 0
    )
    if duplicate_edges:
        raise RuntimeError(f"logic_edges.parquet contains {duplicate_edges} duplicate edges")
    invalid_candidates = int(
        db.scalar(
            """
            SELECT count(*)
            FROM relation_candidates_v
            WHERE proposition_a_id >= proposition_b_id
            """
        )
        or 0
    )
    if invalid_candidates:
        raise RuntimeError(
            f"relation_candidates.parquet contains {invalid_candidates} "
            "non-canonical pairs"
        )
    invalid_execution_rows = int(
        db.scalar(
            """
            SELECT count(*)
            FROM execution_plan_v
            WHERE status = 'reused'
              AND (
                    input_fingerprint IS NULL
                    OR input_fingerprint != output_fingerprint
              )
            """
        )
        or 0
    )
    if invalid_execution_rows:
        raise RuntimeError(
            "execution_plan.parquet contains invalid reuse evidence"
        )


def _discovery_manifest(
    input_path: Path,
    input_hash: str,
    artifact_hashes: dict[str, str],
    source_schema: str,
    stats: dict[str, object],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    timings: dict[str, float],
    state_hashes: dict[str, str],
    inference: _InferenceContext,
) -> dict[str, object]:
    compute = _compute_accounting(config, state, timings)
    artifact_names = [
        *DISCOVERY_PARQUET_ARTIFACTS,
        *STATE_ARTIFACTS,
        GRAPH_DATABASE_ARTIFACT,
        GRAPH_SNAPSHOT_ARTIFACT,
        "primary_model_manifest.json",
        "verifier_model_manifest.json",
        "automation_profile.json",
        "qualification_report.json",
        *(("compute_profile.json",) if config.compute_profile is not None else ()),
    ]
    manifest = {
        "command": "discover",
        "version": __version__,
        "input": str(input_path),
        "input_hash": input_hash,
        "input_schema": source_schema,
        "models": {
            "primary_parse": {
                "requested": config.primary_model,
                "observed": sorted(state.observed_primary_parse_models),
            },
            "verifier_parse": {
                "requested": config.verifier_model,
                "observed": sorted(state.observed_verifier_parse_models),
            },
            "primary_classify": {
                "requested": config.primary_model,
                "observed": sorted(state.observed_primary_classify_models),
            },
            "verifier_classify": {
                "requested": config.verifier_model,
                "observed": sorted(state.observed_verifier_classify_models),
            },
            "embedding": {
                "model": config.embedding_model,
                "revision": config.embedding_revision,
            },
            "nli": {
                "model": config.nli_model,
                "revision": config.nli_revision,
            },
        },
        "prompts": {
            "parse": {
                "version": PARSE_PROMPT_VERSION,
                "hash": _text_hash(_PARSE_PROMPT),
                "request_schema_hash": parse_request_hash(),
                "schema_hash": _model_schema_hash(ParsedMarket),
            },
            "classify": {
                "version": CLASSIFY_PROMPT_VERSION,
                "hash": _text_hash(_CLASSIFY_PROMPT),
                "request_schema_hash": classify_request_hash(),
                "schema_hash": _model_schema_hash(AtomicPairAssessment),
            },
        },
        "inference": {
            "primary": {
                "origin": inference.primary_manifest.inference_origin,
                "runtime": inference.primary_manifest.runtime,
                "runtime_version": inference.primary_manifest.runtime_version,
                "manifest_id": inference.primary_manifest.manifest_id,
                "manifest_hash": manifest_sha256(inference.primary_manifest),
            },
            "verifier": {
                "origin": inference.verifier_manifest.inference_origin,
                "runtime": inference.verifier_manifest.runtime,
                "runtime_version": inference.verifier_manifest.runtime_version,
                "manifest_id": inference.verifier_manifest.manifest_id,
                "manifest_hash": manifest_sha256(inference.verifier_manifest),
            },
            "automation_profile_id": (
                inference.profile.profile_id if inference.profile else None
            ),
            "qualification_status": (
                inference.profile.status if inference.profile else None
            ),
            "fingerprints": inference.fingerprints,
            "sampling": {
                "seed": config.sampling_seed,
                "temperature": config.temperature,
                "top_p": config.generation_top_p,
                "top_k": config.generation_top_k,
                "presence_penalty": config.presence_penalty,
                "parse_max_output_tokens": config.parse_max_output_tokens,
                "classify_max_output_tokens": config.classify_max_output_tokens,
            },
            "remote_inference_allowed": config.allow_remote_inference,
            "proprietary_cache_lineage": False,
        },
        "versions": {
            "normalization": NORMALIZATION_VERSION,
            "domain_taxonomy": DOMAIN_TAXONOMY_VERSION,
            "rules": RULE_VERSION,
            "retrieval": RETRIEVAL_VERSION,
            "candidate_state": CANDIDATE_STATE_VERSION,
            "execution_plan": EXECUTION_PLAN_VERSION,
            "publication": PUBLICATION_VERSION,
            "cache": CACHE_ENTRY_VERSION,
            "rule_registry_hash": _text_hash(
                json.dumps(RULE_REGISTRY, sort_keys=True)
            ),
            "constraints": CONSTRAINT_VERSION,
            "solver": SOLVER_VERSION,
        },
        "limits": {
            "accept_confidence": config.accept_confidence,
            "relation_thresholds": dict(sorted(config.relation_thresholds.items())),
            "parse_confidence": config.parse_confidence,
            "top_k": config.top_k,
            "embedding_block_size": config.embedding_block_size,
            "max_propositions": config.max_propositions,
            "max_candidates": config.max_candidates,
            "max_llm_pairs": config.max_llm_pairs,
            "nli_candidate_pool": min(
                config.max_candidates,
                config.max_llm_pairs * 4,
            ),
            "llm_concurrency": config.llm_concurrency,
        },
        "incremental": {
            "baseline": (
                str(config.incremental_from.resolve())
                if config.incremental_from is not None
                else None
            ),
        },
        "qualification": inference.qualification_report,
        "compute": compute,
        "solver": stats.get("solver"),
        "rules": stats.get("rules"),
        "cache": {
            "directory": str(cache.directory),
            "offline": config.offline,
            **cache.stats(),
        },
        "usage": state.usage_manifest(),
        "artifacts": artifact_names,
        "artifact_hashes": artifact_hashes,
        "state_hashes": state_hashes,
        "reports": list(reports()),
        "stats": stats,
        "stage_timings": timings,
        "stage_metrics": stats.get("stage_metrics"),
    }
    serialized = json.loads(json.dumps(manifest, default=str))
    if not isinstance(serialized, dict):
        raise RuntimeError("Discovery manifest must serialize as an object")
    return {str(key): value for key, value in serialized.items()}


def _compute_accounting(
    config: DiscoveryConfig,
    state: RunState,
    timings: dict[str, float],
) -> dict[str, object] | None:
    if config.compute_profile is None:
        return None
    profile = load_compute_profile(config.compute_profile)
    return compute_accounting(
        profile,
        profile_hash=_sha256(config.compute_profile.resolve()),
        timings=timings,
        usage=state.usage_manifest(),
        peak_rss_mb=_peak_rss_mb(),
    )


def _dedupe_quarantines(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        {str(row["quarantine_id"]): row for row in rows}.values(),
        key=lambda row: str(row["quarantine_id"]),
    )


def _parse_error_rows(
    propositions: Sequence[dict[str, Any]],
    assessments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    proposition_by_id = {
        str(row["proposition_id"]): row for row in propositions
    }
    rows = []
    for assessment in assessments:
        if assessment.get("status") == "valid":
            continue
        proposition_id = str(assessment["proposition_id"])
        proposition = proposition_by_id[proposition_id]
        kind = "parse_assessment_invalid"
        message = str(assessment.get("validation_error") or "invalid model parse")
        rows.append(
            {
                "error_id": _text_hash(
                    f"{proposition_id}|{assessment['model_role']}|{message}"
                ),
                "proposition_id": proposition_id,
                "market_id": proposition["market_id"],
                "error_kind": kind,
                "error_message": message,
                "cache_state": None,
                "error_type": None,
                "status_code": None,
                "response_json": assessment.get("parsed_json"),
                "question": proposition["question"],
                "description": proposition["description"],
                "parse_confidence": proposition["parse_confidence"],
                "market_source_hash": proposition["market_source_hash"],
                "model_role": assessment["model_role"],
                "parser_model": assessment["model_version"],
                "prompt_version": proposition["prompt_version"],
                "schema_version": _model_schema_hash(ParsedMarket),
                "normalization_version": proposition["normalization_version"],
            }
        )
    return sorted(rows, key=lambda row: str(row["error_id"]))


async def _run_batched(
    payloads: list[dict[str, object]],
    batch_size: int,
    concurrency: int,
    call: Callable[[list[dict[str, object]]], Awaitable[Any]],
) -> list[tuple[list[dict[str, object]], Any]]:
    batches = [
        payloads[index : index + batch_size]
        for index in range(0, len(payloads), batch_size)
    ]
    semaphore = asyncio.Semaphore(concurrency)

    async def run(batch: list[dict[str, object]]) -> tuple[list[dict[str, object]], Any]:
        async with semaphore:
            try:
                return batch, await call(batch)
            except Exception as exc:  # preserve failures as auditable diagnostics
                return batch, exc

    return await asyncio.gather(*(run(batch) for batch in batches))


async def _run_inference_stage(
    client: StructuredClient,
    payloads: list[dict[str, object]],
    call: Callable[[list[dict[str, object]]], Awaitable[Any]],
    *,
    concurrency: int,
    close_after: bool,
) -> list[tuple[list[dict[str, object]], Any]]:
    try:
        results: list[tuple[list[dict[str, object]], Any]] = []
        window_size = max(concurrency, concurrency * 8)
        for start in range(0, len(payloads), window_size):
            results.extend(
                await _run_batched(
                    payloads[start : start + window_size],
                    1,
                    concurrency,
                    call,
                )
            )
        return results
    finally:
        if close_after:
            await client.aclose()


async def _with_retries(call: Callable[[], Awaitable[Any]]) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await call()
        except Exception as exc:
            last_error = exc
            if attempt == 2 or not _is_transient_error(exc):
                raise
            progress = _ACTIVE_PROGRESS.get()
            if progress is not None:
                progress.event(
                    "retry",
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    status_code=getattr(exc, "status_code", None),
                )
            await asyncio.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def _is_transient_error(exc: Exception) -> bool:
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return retryable
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (
        status_code in {408, 409, 429} or status_code >= 500
    ):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


_AsyncResult = TypeVar("_AsyncResult")


def _close_structured_client(client: StructuredClient) -> None:
    _run_async(client.aclose())


def _run_async(
    awaitable: Coroutine[Any, Any, _AsyncResult],
) -> _AsyncResult:
    return asyncio.run(awaitable)
