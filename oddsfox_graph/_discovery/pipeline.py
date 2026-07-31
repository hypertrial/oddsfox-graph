from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from . import publication as discovery_publication
from .bulk import create_and_fill as _create_and_fill
from .cache import (
    CacheState,
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
)
from .inference import (
    LocalStructuredClient,
    ModelManifest,
    ModelProfile,
    StructuredClient,
    inference_fingerprint,
    load_compute_profile,
    load_model_manifest,
    load_model_profile,
    manifest_sha256,
    normalize_inference_base_url,
    validate_profile_match,
)
from .protocol import (
    CLASSIFY_PROMPT as _CLASSIFY_PROMPT,
    PARSE_PROMPT as _PARSE_PROMPT,
    classify_request_hash,
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
from .adjudication import (
    classification_validation_error as _classification_validation_error,
    derive_atomic_relation as _derive_atomic_relation,
    nli_text as _nli_text,
)
from .parsing import (
    proposition_row as _proposition_row,
    validate_parsed_market as _validate_parsed_market,
)
from .retrieval import generate_candidate_workspace
from .metrics import RunState, StageRecorder
from .nli import (
    ModernBertNliScorer,
    NliScorer,
    nli_inference_fingerprint,
    profiled_nli_action,
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
    AdjudicationStageResult,
    IncrementalPreparation,
    IncrementalResources,
    IncrementalStats,
    ParsingStageResult,
    PublicationStageResult,
    RetrievalStageResult,
    SolvingStageResult,
)
from .relations import (
    HARD_FACT_RULE_IDS,
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
    CACHE_ENTRY_VERSION,
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
    "review_queue.parquet",
    "rejected_edges.parquet",
    "parse_errors.parquet",
)
GRAPH_DATABASE_ARTIFACT = "oddsfox_graph.duckdb"
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
    "parser_model": "VARCHAR",
    "prompt_version": "VARCHAR",
    "inference_fingerprint": "VARCHAR",
    "model_profile_id": "VARCHAR",
    "source_schema": "VARCHAR",
}

REVIEW_COLUMNS = {
    "review_id": "VARCHAR",
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "review_kind": "VARCHAR",
    "proposed_relation": "VARCHAR",
    "confidence": "DOUBLE",
    "explanation": "VARCHAR",
    "assumptions": "VARCHAR[]",
    "model_version": "VARCHAR",
    "prompt_version": "VARCHAR",
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
    "model_version": "VARCHAR",
    "prompt_version": "VARCHAR",
    "rejection_reason": "VARCHAR",
    "conflicting_proposal_ids": "VARCHAR[]",
    "conflicting_constraint_ids": "VARCHAR[]",
    "solver_component_id": "VARCHAR",
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

LOGIC_EDGE_COLUMNS = dict(
    zip(
        ARTIFACT_COLUMNS["logic_edges.parquet"],
        (
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "DOUBLE",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR[]",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
        ),
        strict=True,
    )
)


@dataclass(frozen=True)
class _InferenceContext:
    manifest: ModelManifest
    profile: ModelProfile | None
    fingerprints: dict[str, str]
    client: StructuredClient | None
    owns_client: bool

def _prepare_inference_context(
    config: DiscoveryConfig,
    out_dir: Path,
    injected_client: StructuredClient | None,
) -> _InferenceContext:
    manifest_path = config.model_manifest
    if (
        manifest_path is None
        and config.offline
        and (out_dir / "model_manifest.json").is_file()
    ):
        manifest_path = out_dir / "model_manifest.json"
    using_synthetic_manifest = (
        manifest_path is None and injected_client is not None
    )
    if manifest_path is not None:
        manifest = load_model_manifest(manifest_path)
        normalized_origin = (
            manifest.inference_origin
            if config.offline
            else normalize_inference_base_url(
                config.llm_base_url,
                allow_remote=config.allow_remote_inference,
            )
        )
    elif injected_client is not None:
        normalized_origin = normalize_inference_base_url(
            config.llm_base_url,
            allow_remote=config.allow_remote_inference,
        )
        synthetic_hash = _text_hash("injected-self-hosted-test-model")
        synthetic_manifest_content = {
            "model_id": config.parse_model,
            "upstream_revision": "test-fixture",
            "artifact_sha256": synthetic_hash,
            "artifact_kind": "fixture",
            "quantization": "fixture",
            "license": "Apache-2.0",
            "tokenizer_sha256": synthetic_hash,
            "chat_template_sha256": synthetic_hash,
            "runtime": config.llm_runtime,
            "runtime_version": "test-fixture",
            "loaded_model_identifier": config.parse_model,
            "context_length": 8192,
            "deployment": "in-process network-free test fixture",
            "inference_origin": normalized_origin,
        }
        manifest = ModelManifest.model_validate(
            {
                "manifest_id": canonical_json_sha256(
                    synthetic_manifest_content
                ),
                **synthetic_manifest_content,
            }
        )
    else:
        if config.offline:
            raise ValueError(
                "Offline discovery cache has no model_manifest.json; rerun "
                "online into --out first or pass --model-manifest"
            )
        raise ValueError("--model-manifest is required for online discovery")
    if not config.offline and manifest.runtime != config.llm_runtime:
        raise ValueError(
            "Configured LLM runtime does not match the model manifest"
        )
    if not config.offline and manifest.inference_origin != normalized_origin:
        raise ValueError(
            "Configured LLM endpoint does not match the model manifest"
        )
    allowed_model_ids = {manifest.model_id, manifest.loaded_model_identifier}
    if not using_synthetic_manifest:
        if config.parse_model not in allowed_model_ids:
            raise ValueError("Parse model does not match the model manifest")
        if config.classify_model not in allowed_model_ids:
            raise ValueError("Classify model does not match the model manifest")

    fingerprints = {
        "parse": inference_fingerprint(
            manifest,
            role="parse",
            requested_model=config.parse_model,
            prompt_version=PARSE_PROMPT_VERSION,
            prompt_hash=_text_hash(_PARSE_PROMPT),
            request_schema_hash=parse_request_hash(),
            schema_hash=_model_schema_hash(ParsedMarket),
            settings=_generation_settings(config, role="parse"),
        ),
        "classify": inference_fingerprint(
            manifest,
            role="classify",
            requested_model=config.classify_model,
            prompt_version=CLASSIFY_PROMPT_VERSION,
            prompt_hash=_text_hash(_CLASSIFY_PROMPT),
            request_schema_hash=classify_request_hash(),
            schema_hash=_model_schema_hash(AtomicPairAssessment),
            settings=_generation_settings(config, role="classify"),
        ),
        "nli": nli_inference_fingerprint(
            config.nli_model,
            config.nli_revision,
        ),
    }
    profile_path = config.model_profile
    if (
        profile_path is None
        and config.offline
        and (out_dir / "model_profile.json").is_file()
    ):
        profile_path = out_dir / "model_profile.json"
    if profile_path is not None:
        profile = load_model_profile(profile_path)
        validate_profile_match(
            profile,
            manifest,
            fingerprints,
            {
                "parse": parse_request_hash(),
                "classify": classify_request_hash(),
            },
            parse_prompt_hash=_text_hash(_PARSE_PROMPT),
            parse_schema_hash=_model_schema_hash(ParsedMarket),
            classify_prompt_hash=_text_hash(_CLASSIFY_PROMPT),
            classify_schema_hash=_model_schema_hash(AtomicPairAssessment),
        )
    elif injected_client is not None and config.model_manifest is None:
        synthetic_profile_content = {
            "model_manifest_id": manifest.manifest_id,
            "model_manifest_sha256": manifest_sha256(manifest),
            "runtime": manifest.runtime,
            "runtime_version": manifest.runtime_version,
            "benchmark_sha256": _text_hash("test-benchmark"),
            "calibration_partition_sha256": _text_hash("test-calibration"),
            "parse_prompt_hash": _text_hash(_PARSE_PROMPT),
            "parse_schema_hash": _model_schema_hash(ParsedMarket),
            "classify_prompt_hash": _text_hash(_CLASSIFY_PROMPT),
            "classify_schema_hash": _model_schema_hash(AtomicPairAssessment),
            "request_contract_hashes": {
                "parse": parse_request_hash(),
                "classify": classify_request_hash(),
            },
            "inference_fingerprints": fingerprints,
            "relations": {
                relation: {
                    "enabled": True,
                    "threshold": 0.0,
                    "support": 100,
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
            "nli_actions": {},
            "structured_output_validity": 1.0,
            "metrics": {"fixture": True},
        }
        profile = ModelProfile.model_validate(
            {
                "profile_id": canonical_json_sha256(
                    synthetic_profile_content
                ),
                **synthetic_profile_content,
            }
        )
    else:
        profile = None
    if config.require_ready and profile is None:
        raise ValueError("--require-ready requires an exact matching --model-profile")

    client = injected_client
    owns_client = False
    if not config.offline and client is None:
        client = LocalStructuredClient(
            normalized_origin,
            allow_remote=config.allow_remote_inference,
        )
        owns_client = True
    if not config.offline and client is not None and hasattr(client, "preflight"):
        preflight = _run_async(
            _preflight_stage_client(
                client,
                config,
                close_after=owns_client,
            )
        )
        if isinstance(preflight, dict):
            runtime_version = preflight.get("runtime_version")
            if runtime_version and runtime_version != manifest.runtime_version:
                raise ValueError(
                    "Endpoint runtime version does not match the model manifest"
                )
        if owns_client:
            client = None
    return _InferenceContext(
        manifest=manifest,
        profile=profile,
        fingerprints=fingerprints,
        client=client,
        owns_client=owns_client,
    )


async def _preflight_stage_client(
    client: StructuredClient,
    config: DiscoveryConfig,
    *,
    close_after: bool,
) -> object:
    try:
        return await _with_retries(
            lambda: client.preflight(
                expected_model=config.parse_model,
                expected_runtime=config.llm_runtime,
            )
        )
    finally:
        if close_after:
            await client.aclose()


def discover(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig | None = None,
    _client: StructuredClient | None = None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None = None,
    _nli_scorer: NliScorer | None = None,
) -> dict[str, object]:
    config = config or DiscoveryConfig()
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"Input parquet does not exist: {input_path}")
    config = _with_packaged_benchmark(config, input_path)
    config.validate()

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    recorder = StageRecorder()
    inference = recorder.run(
        "model_preflight",
        lambda: _prepare_inference_context(config, out_dir, _client),
    )

    source_schema, input_rows, markets, input_selection = recorder.run(
        "normalize_input",
        lambda: _load_source_markets(
            input_path,
            max_propositions=config.max_propositions,
        ),
    )
    cache_dir = (config.cache_dir or Path(str(out_dir) + ".cache")).resolve()
    cache = InferenceCache(cache_dir, offline=config.offline)
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
    parse_reviews = parse_result.reviews
    rule_support = recorder.run(
        "benchmark_rule_gates",
        lambda: _apply_benchmark_rule_gates(
            propositions,
            config,
        ),
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
    candidate_store.mark_classification_budget()
    nli_pool_limit = min(
        config.max_candidates,
        config.max_llm_pairs * 4,
    )
    nli_result = recorder.run(
        "score_nli",
        lambda: _score_nli_candidate_batches(
            candidate_store,
            propositions,
            config,
            cache,
            inference,
            _nli_scorer,
            injected_client=_client is not None,
            limit=nli_pool_limit,
        ),
    )
    nli_edges = nli_result.edges
    nli_reviews = nli_result.reviews
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
    llm_reviews = classification_result.reviews
    llm_edges = nli_edges + generated_edges
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
            deterministic_edges + llm_edges,
            reusable_solver_components=reusable_solver_components,
        ),
    )
    logic_edges = solving_result.accepted_edges
    rejected_edges = solving_result.rejected_edges
    consistency_reviews = solving_result.reviews
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
    review_rows = _dedupe_reviews(
        parse_reviews + nli_reviews + llm_reviews + consistency_reviews
    )
    parse_error_rows = _parse_error_rows(propositions, parse_reviews)
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
            review_rows,
            proposition_fingerprint_state,
            candidate_component_state,
            solver_component_state,
            execution_plan_rows,
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
                review_rows,
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
        evaluation: dict[str, Any] | None = None
        if config.benchmark_path is not None:
            from ..evaluation import evaluate_build

            benchmark_path = config.benchmark_path
            evaluation = recorder.run(
                "evaluate_benchmark",
                lambda: evaluate_build(
                    staging,
                    benchmark_path,
                    input_hash=input_hash,
                    compute_profile=config.compute_profile,
                    run_metadata={
                        "usage": state.usage_manifest(),
                        "models": {
                            "parse": {
                                "requested": config.parse_model,
                                "observed": sorted(state.observed_parse_models),
                            },
                            "classify": {
                                "requested": config.classify_model,
                                "observed": sorted(state.observed_classify_models),
                            },
                        },
                        "inference": {
                            "origin": inference.manifest.inference_origin,
                            "runtime": inference.manifest.runtime,
                            "runtime_version": inference.manifest.runtime_version,
                            "profiled": inference.profile is not None,
                            "model_profile_id": (
                                inference.profile.profile_id
                                if inference.profile
                                else None
                            ),
                            "fingerprints": inference.fingerprints,
                            "proprietary_cache_lineage": False,
                        },
                        "stage_timings": recorder.timings,
                        "stats": stats,
                        "validation": {
                            "offline": config.offline,
                            "max_propositions": config.max_propositions,
                        },
                    },
                ),
            )
            stats["evaluation_exit_decision"] = evaluation["exit_decision"]
        elif config.require_ready:
            raise ValueError("--require-ready requires --benchmark")
        artifact_hashes = recorder.run(
            "hash_artifacts",
            lambda: {
                name: _sha256(staging / name)
                for name in (
                    *DISCOVERY_PARQUET_ARTIFACTS,
                    "model_manifest.json",
                    *(
                        ("model_profile.json",)
                        if (staging / "model_profile.json").is_file()
                        else ()
                    ),
                    *(
                        ("compute_profile.json",)
                        if (staging / "compute_profile.json").is_file()
                        else ()
                    ),
                    *(
                        ("benchmark.parquet",)
                        if (staging / "benchmark.parquet").is_file()
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
        if config.require_ready and (
            evaluation is None or evaluation["exit_decision"] != "READY_TO_SCALE"
        ):
            raise RuntimeError(
                "Discovery quality gates did not produce READY_TO_SCALE"
            )
        manifest_stats = manifest["stats"]
        if not isinstance(manifest_stats, dict):
            raise RuntimeError("Discovery manifest stats must be an object")
        return {str(key): value for key, value in manifest_stats.items()}
    finally:
        candidate_store.close()
        cache.close()
        shutil.rmtree(staging, ignore_errors=True)


def _with_packaged_benchmark(
    config: DiscoveryConfig,
    input_path: Path,
) -> DiscoveryConfig:
    if config.benchmark_path is not None:
        return config
    packaged = Path(__file__).parents[1] / "benchmarks" / "v0.8.0.parquet"
    if not packaged.is_file():
        return config
    db = DuckDB()
    try:
        source_hashes = {
            str(row["source_sha256"])
            for row in db.rows(
                f"""
                SELECT DISTINCT source_sha256
                FROM read_parquet('{q(packaged)}')
                """
            )
        }
    finally:
        db.close()
    if source_hashes == {_sha256(input_path)}:
        return replace(config, benchmark_path=packaged)
    return config


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
        prior_classifications = (
            []
            if config.offline
            else db.rows(
                f"""
                SELECT proposition_a_id, proposition_b_id,
                       classification_relation, classification_confidence,
                       atomic_a_implies_b, atomic_b_implies_a,
                       atomic_can_both_be_true, atomic_must_one_be_true,
                       atomic_logically_related,
                       supporting_fields, a_implies_b, b_implies_a,
                       explanation, assumptions, requires_review,
                       unsupported_assumption, model_version, prompt_version,
                       inference_fingerprint, model_profile_id
                FROM read_parquet('{q(candidates_path)}')
                WHERE classification_relation IS NOT NULL
                ORDER BY proposition_a_id, proposition_b_id
                """
            )
        )
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
        (previous_inference.get("fingerprints") or {}).get("parse")
        == inference.fingerprints["parse"]
    )
    seeded = 0
    if parse_compatible and not config.offline:
        seeded = _seed_parse_cache_from_baseline(
            cache,
            markets,
            prior_propositions,
            unchanged_market_ids,
            config,
            inference,
        )

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
        (previous_inference.get("fingerprints") or {}).get("classify")
        == inference.fingerprints["classify"]
    )
    if not classification_compatible:
        reasons.append("classifier_model_prompt_or_schema")
    previous_profile_id = previous_inference.get("model_profile_id")
    current_profile_id = (
        inference.profile.profile_id if inference.profile is not None else None
    )
    if previous_profile_id != current_profile_id:
        reasons.append("model_profile")
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


def _seed_parse_cache_from_baseline(
    cache: InferenceCache,
    markets: Sequence[SourceMarket],
    prior_propositions: Sequence[dict[str, Any]],
    unchanged_market_ids: set[str],
    config: DiscoveryConfig,
    inference: _InferenceContext,
) -> int:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prior_propositions:
        market_id = str(row["market_id"])
        if market_id in unchanged_market_ids:
            by_market[market_id].append(row)
    schema_hash = _model_schema_hash(ParsedMarket)
    pending: dict[str, dict[str, Any]] = {}
    for market in markets:
        rows = sorted(
            by_market.get(market.market_id, []),
            key=lambda row: int(row["outcome_index"]),
        )
        if len(rows) != len(market.outcomes) or any(
            row.get("parse_status") != "parsed" for row in rows
        ):
            continue
        payload = _market_payload(market)
        key = cache.key(
            "parse",
            inference.fingerprints["parse"],
            PARSE_PROMPT_VERSION,
            _text_hash(_PARSE_PROMPT),
            schema_hash,
            payload,
        )
        parsed = ParsedMarket(
            market_id=market.market_id,
            propositions=[
                ParsedOutcome(
                    outcome=str(row["outcome"]),
                    subject=list(row.get("subject_original") or row.get("subject") or []),
                    predicate=_str_or_none(row.get("predicate")),
                    object=_str_or_none(row.get("object_original")),
                    operator=row.get("operator"),
                    threshold=row.get("threshold"),
                    unit=_str_or_none(row.get("unit_original")),
                    time_start=row.get("time_start"),
                    time_end=row.get("time_end"),
                    competition=_str_or_none(row.get("competition_original")),
                    event_scope=_str_or_none(row.get("event_scope_original")),
                    jurisdiction=_str_or_none(row.get("jurisdiction_original")),
                    polarity=cast(
                        Literal["positive", "negative"],
                        str(row["polarity"]),
                    ),
                    parse_confidence=float(row["parse_confidence"]),
                )
                for row in rows
            ],
        )
        pending[key] = cache_entry(
            task="parse",
            parsed=parsed.model_dump(mode="json"),
            error=None,
            observed_model=str(rows[0]["parser_model"]),
            usage={},
            usage_scope=None,
            state="success",
        )
    existing = cache.contains_many(tuple(pending))
    writes = {
        key: entry for key, entry in pending.items() if key not in existing
    }
    cache.put_many(writes)
    seeded = len(writes)
    return seeded


def _parse_propositions(
    markets: Sequence[SourceMarket],
    source_schema: str,
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> ParsingStageResult:
    schema_hash = _model_schema_hash(ParsedMarket)
    cached: dict[str, dict[str, Any]] = {}
    missing: list[tuple[SourceMarket, str, dict[str, object]]] = []
    requests: list[tuple[SourceMarket, str, dict[str, object]]] = []
    for market in markets:
        payload = _market_payload(market)
        key = cache.key(
            "parse",
            inference.fingerprints["parse"],
            PARSE_PROMPT_VERSION,
            _text_hash(_PARSE_PROMPT),
            schema_hash,
            payload,
        )
        requests.append((market, key, payload))
    entries = cache.get_many(
        [key for _, key, _ in requests],
        offline=config.offline,
    )
    for market, key, payload in requests:
        entry = entries.get(key)
        if entry is None:
            missing.append((market, key, payload))
        else:
            cached[market.market_id] = entry
            state.add_cached_usage(
                dict(entry.get("usage") or {}),
                _str_or_none(entry.get("usage_scope")),
                "parse",
            )

    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} proposition parse entries"
            )
        stage_client = inference.client
        close_stage_client = False
        if stage_client is None:
            stage_client = LocalStructuredClient(
                config.llm_base_url,
                allow_remote=config.allow_remote_inference,
            )
            close_stage_client = True
        missing_by_market = {
            item[0].market_id: item
            for item in missing
        }
        results = _run_async(
            _run_inference_stage(
                stage_client,
                [item[2] for item in missing],
                lambda batch: _local_parse_market(
                    stage_client,
                    batch[0],
                    config,
                ),
                concurrency=config.llm_concurrency,
                close_after=close_stage_client,
            )
        )
        pending_cache_writes: dict[str, dict[str, Any]] = {}
        for batch_items, result in results:
            by_id: dict[str, dict[str, object]] = {}
            observed_model = config.parse_model
            usage: dict[str, int] = {}
            error: str | None = None
            error_state: CacheState = "stable_failure"
            error_type: str | None = None
            status_code: int | None = None
            if isinstance(result, Exception):
                error = str(result)
                error_state = (
                    "transient_failure"
                    if _is_transient_error(result)
                    else "stable_failure"
                )
                error_type = type(result).__name__
                raw_status = getattr(result, "status_code", None)
                status_code = raw_status if isinstance(raw_status, int) else None
            else:
                parsed, observed_model, usage = result
                by_id = {
                    parsed.market_id: parsed.model_dump(mode="json")
                }
                state.observed_parse_models.add(observed_model)
                state.add_usage(usage, "parse")
            usage_scope = cache.usage_scope("parse", batch_items)
            for payload in batch_items:
                market_id = str(payload["market_id"])
                source_market, key, _ = missing_by_market[market_id]
                market_error = error
                parsed_payload = by_id.get(market_id)
                if parsed_payload is None and market_error is None:
                    market_error = "structured output omitted this market"
                entry = cache_entry(
                    task="parse",
                    parsed=parsed_payload,
                    error=market_error,
                    observed_model=observed_model,
                    usage=usage,
                    usage_scope=usage_scope,
                    state=(
                        error_state
                        if error is not None
                        else (
                            "success"
                            if parsed_payload is not None
                            else "stable_failure"
                        )
                    ),
                    error_type=error_type,
                    status_code=status_code,
                )
                pending_cache_writes[key] = entry
                cached[source_market.market_id] = entry
        cache.put_many(pending_cache_writes)

    propositions: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for market in markets:
        entry = cached[market.market_id]
        observed_model = str(entry.get("observed_model") or config.parse_model)
        state.observed_parse_models.add(observed_model)
        parsed_market: ParsedMarket | None = None
        error = cache_error(entry)
        if not error and entry.get("parsed") is not None:
            try:
                parsed_market = ParsedMarket.model_validate(entry["parsed"])
                _validate_parsed_market(market, parsed_market)
            except (ValueError, TypeError) as exc:
                error = str(exc)
        cache_error_payload = entry.get("error")
        error_type = (
            str(cache_error_payload.get("type"))
            if isinstance(cache_error_payload, dict)
            and cache_error_payload.get("type")
            else ("ValidationError" if error and entry.get("parsed") is not None else None)
        )
        status_code = (
            cache_error_payload.get("status_code")
            if isinstance(cache_error_payload, dict)
            else None
        )
        if error:
            parse_review_kind = (
                "parse_omission"
                if "omitted" in error
                else (
                    "parse_validation"
                    if entry.get("parsed") is not None
                    else "parse_response_error"
                )
            )
        else:
            parse_review_kind = "parse_error"
        parsed_by_outcome = (
            {item.outcome: item for item in parsed_market.propositions}
            if parsed_market
            else {}
        )
        for source_outcome in market.outcomes:
            parsed = parsed_by_outcome.get(source_outcome.outcome)
            proposition = _proposition_row(
                market,
                source_outcome,
                parsed,
                observed_model,
                source_schema,
                error,
                inference.fingerprints["parse"],
                inference.profile.profile_id if inference.profile else None,
            )
            proposition["_parse_cache_state"] = entry.get("state")
            proposition["_parse_error_type"] = error_type
            proposition["_parse_status_code"] = status_code
            proposition["_parse_response_json"] = (
                json.dumps(entry["parsed"], sort_keys=True)
                if entry.get("parsed") is not None
                else None
            )
            propositions.append(proposition)
            disagreements = list(proposition.pop("_authoritative_disagreements", []))
            if disagreements:
                reviews.append(
                    _review_row(
                        "parse_authoritative_disagreement",
                        proposition["proposition_id"],
                        None,
                        None,
                        float(proposition["parse_confidence"]),
                        "; ".join(disagreements),
                        [],
                        observed_model,
                        PARSE_PROMPT_VERSION,
                    )
                )
            if error or parsed is None:
                reviews.append(
                    _review_row(
                        parse_review_kind,
                        proposition["proposition_id"],
                        None,
                        None,
                        0.0,
                        error or "structured output omitted this outcome",
                        [],
                        observed_model,
                        PARSE_PROMPT_VERSION,
                    )
                )
            elif float(proposition["parse_confidence"]) < config.parse_confidence:
                reviews.append(
                    _review_row(
                        "parse_low_confidence",
                        proposition["proposition_id"],
                        None,
                        None,
                        float(proposition["parse_confidence"]),
                        "Proposition parse confidence is below the acceptance threshold",
                        [],
                        observed_model,
                        PARSE_PROMPT_VERSION,
                    )
                )
    return ParsingStageResult(
        propositions=sorted(
            propositions,
            key=lambda row: str(row["proposition_id"]),
        ),
        reviews=reviews,
    )


def _market_payload(market: SourceMarket) -> dict[str, object]:
    return market_request(market).model_dump(mode="json")


async def _local_parse_market(
    client: StructuredClient,
    payload: dict[str, object],
    config: DiscoveryConfig,
) -> tuple[ParsedMarket, str, dict[str, int]]:
    result = await _with_retries(
        lambda: client.generate(
            model=config.parse_model,
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


def _apply_benchmark_rule_gates(
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
) -> dict[str, Any]:
    benchmark_path = config.benchmark_path
    hard_rules = set(HARD_FACT_RULE_IDS)
    if benchmark_path is None:
        enabled = (
            set(RULE_REGISTRY)
            if config.allow_unbenchmarked_rules
            else hard_rules
        )
        return {
            "benchmark_enforced": False,
            "allow_unbenchmarked_rules": config.allow_unbenchmarked_rules,
            "ready_eligible": not config.allow_unbenchmarked_rules,
            "minimum_positive_examples": 10,
            "minimum_adversarial_examples": 10,
            "enabled": sorted(enabled),
            "experimental": sorted(set(RULE_REGISTRY) - enabled),
            "support": {},
        }
    benchmark_path = benchmark_path.resolve()
    if not benchmark_path.is_file():
        raise ValueError(f"Benchmark does not exist: {benchmark_path}")
    db = DuckDB()
    try:
        benchmark_pairs = db.rows(
            f"""
            SELECT proposition_a_id, proposition_b_id, expected_relation
            FROM read_parquet('{q(benchmark_path)}')
            WHERE record_type = 'pair'
            ORDER BY proposition_a_id, proposition_b_id
            """
        )
    finally:
        db.close()
    by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    support = {
        rule_id: {"positive": 0, "adversarial": 0}
        for rule_id in RULE_REGISTRY
    }
    for benchmark in benchmark_pairs:
        a_id, b_id = sorted(
            (
                str(benchmark["proposition_a_id"]),
                str(benchmark["proposition_b_id"]),
            )
        )
        if a_id not in by_id or b_id not in by_id:
            continue
        relation = _deterministic_relation(
            by_id[a_id],
            by_id[b_id],
            config.parse_confidence,
        )
        expected = str(benchmark["expected_relation"])
        if relation is not None:
            rule_id = str(relation["rule_id"])
            if _relation_matches_benchmark(relation, a_id, b_id, expected):
                support[rule_id]["positive"] += 1
            else:
                support[rule_id]["adversarial"] += 1
        for rule_id in RULE_REGISTRY:
            if relation is not None and relation.get("rule_id") == rule_id:
                continue
            if _rule_near_applicable(rule_id, by_id[a_id], by_id[b_id]):
                support[rule_id]["adversarial"] += 1

    enabled = hard_rules | {
        rule_id
        for rule_id, counts in support.items()
        if counts["positive"] >= 10 and counts["adversarial"] >= 10
    }
    if config.allow_unbenchmarked_rules:
        enabled = set(RULE_REGISTRY)
    return {
        "benchmark_enforced": True,
        "allow_unbenchmarked_rules": config.allow_unbenchmarked_rules,
        "ready_eligible": not config.allow_unbenchmarked_rules,
        "minimum_positive_examples": 10,
        "minimum_adversarial_examples": 10,
        "enabled": sorted(enabled),
        "experimental": sorted(set(RULE_REGISTRY) - enabled),
        "support": support,
    }


def _relation_matches_benchmark(
    relation: dict[str, Any],
    a_id: str,
    b_id: str,
    expected: str,
) -> bool:
    edge_type = str(relation["edge_type"])
    if edge_type != "implies":
        return edge_type == expected
    observed = (
        "A_implies_B"
        if str(relation["src_node_id"]) == a_id
        and str(relation["dst_node_id"]) == b_id
        else "B_implies_A"
    )
    return observed == expected


def _rule_near_applicable(
    rule_id: str,
    a: dict[str, Any],
    b: dict[str, Any],
) -> bool:
    same_subject = set(a.get("subject") or []) == set(b.get("subject") or [])
    same_predicate = a.get("predicate") == b.get("predicate")
    same_event = bool(
        (a.get("event_id") or a.get("event_slug"))
        and (a.get("event_id") or a.get("event_slug"))
        == (b.get("event_id") or b.get("event_slug"))
    )
    if rule_id == "same_market.binary_complement.v1":
        return same_event and a["market_id"] != b["market_id"]
    if rule_id == "same_market.categorical_exclusion.v1":
        return same_event and a["market_id"] != b["market_id"]
    if rule_id == "equivalence.normalized_fields.v1":
        return same_subject and same_predicate
    if rule_id == "threshold.interval_containment.v2":
        return (
            same_subject
            and same_predicate
            and a.get("unit") == b.get("unit")
            and a.get("threshold") is not None
            and b.get("threshold") is not None
        )
    if rule_id == "time.interval_containment.v1":
        return (
            same_subject
            and same_predicate
            and all(
                (
                    a.get("time_start"),
                    a.get("time_end"),
                    b.get("time_start"),
                    b.get("time_end"),
                )
            )
        )
    if rule_id == "tournament.stage_progression.v1":
        return same_subject and a.get("competition") == b.get("competition")
    if rule_id == "event.single_winner.v1":
        return same_event and (
            _is_winner_proposition(a) or _is_winner_proposition(b)
        )
    return False


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
                model_version=None,
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not candidates:
        return [], []
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

    edges: list[dict[str, Any]] = []
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
        action, relation, confidence = profiled_nli_action(
            columns,
            inference.profile,
        )
        if (
            relation is not None
            and relation != "unrelated"
            and confidence < config.threshold_for(relation)
        ):
            action = "advisory_below_cli_threshold"
            relation = None
        candidate["nli_action"] = action
        if relation is None:
            continue
        candidate.update(
            {
                "classification_relation": relation,
                "classification_confidence": confidence,
                "supporting_fields": json.dumps(
                    [
                        {
                            "proposition": "A",
                            "field": "question",
                            "value": str(by_id[a_id]["question"]),
                        },
                        {
                            "proposition": "B",
                            "field": "question",
                            "value": str(by_id[b_id]["question"]),
                        },
                    ],
                    sort_keys=True,
                ),
                "a_implies_b": relation in {"equivalent", "A_implies_B"},
                "b_implies_a": relation in {"equivalent", "B_implies_A"},
                "explanation": f"Profile-gated local NLI action {action}",
                "assumptions": [],
                "requires_review": False,
                "unsupported_assumption": False,
                "discovery_method": "nli",
                "model_version": f"{config.nli_model}@{config.nli_revision}",
                "prompt_version": "nli-profile-v1",
                "inference_fingerprint": fingerprint,
                "model_profile_id": (
                    inference.profile.profile_id if inference.profile else None
                ),
            }
        )
        if relation == "unrelated":
            candidate["status"] = "rejected"
            continue
        candidate["status"] = "accepted"
        if relation == "A_implies_B":
            edge_type, src, dst = "implies", a_id, b_id
        elif relation == "B_implies_A":
            edge_type, src, dst = "implies", b_id, a_id
        else:
            edge_type, src, dst = "equivalent", *sorted((a_id, b_id))
        edges.append(
            _logic_edge_row(
                {
                    "edge_type": edge_type,
                    "src_node_id": src,
                    "dst_node_id": dst,
                    "edge_basis": "profile_gated_open_nli",
                    "explanation": f"Profile-gated local NLI action {action}",
                    "confidence": confidence,
                },
                by_id,
                discovery_method="nli",
                rule_version=None,
                model_version=f"{config.nli_model}@{config.nli_revision}",
                prompt_version="nli-profile-v1",
                assumptions=[],
                inference_fingerprint_value=fingerprint,
                model_profile_id=(
                    inference.profile.profile_id if inference.profile else None
                ),
            )
        )
    return edges, []


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
) -> AdjudicationStageResult:
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
    edges: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for batch in candidate_store.inference_batches(batch_size=512):
        batch_edges, batch_reviews = _apply_nli_scores(
            batch,
            propositions,
            config,
            cache,
            inference,
            effective_scorer,
            injected_client=injected_client,
        )
        candidate_store.update_nli_rows(batch)
        edges.extend(batch_edges)
        reviews.extend(batch_reviews)
    return AdjudicationStageResult(edges=edges, reviews=reviews)


def _classify_candidate_batches(
    candidate_store: CandidateStore,
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    incremental_stats: IncrementalStats,
    incremental_resources: IncrementalResources,
    inference: _InferenceContext,
) -> AdjudicationStageResult:
    candidate_store.prepare_inference_queue(config.max_llm_pairs)
    edges: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for batch in candidate_store.inference_batches(batch_size=512):
        if not config.offline:
            _seed_classification_cache_from_incremental(
                cache,
                batch,
                propositions,
                config,
                incremental_stats,
                incremental_resources,
                inference,
            )
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
                if row.get("discovery_method") == "generative_model"
            ]
        )
        edges.extend(result.edges)
        reviews.extend(result.reviews)
    return AdjudicationStageResult(edges=edges, reviews=reviews)


def _classify_candidates(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: InferenceCache,
    state: RunState,
    inference: _InferenceContext,
) -> AdjudicationStageResult:
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

    schema_hash = _model_schema_hash(AtomicPairAssessment)
    cached: dict[str, dict[str, Any]] = {}
    missing: list[tuple[dict[str, Any], str, dict[str, object]]] = []
    requests: list[tuple[dict[str, Any], str, dict[str, object]]] = []
    for candidate in selected:
        payload = _pair_payload(candidate, by_id)
        key = cache.key(
            "classify",
            inference.fingerprints["classify"],
            CLASSIFY_PROMPT_VERSION,
            _text_hash(_CLASSIFY_PROMPT),
            schema_hash,
            payload,
        )
        requests.append((candidate, key, payload))
    entries = cache.get_many(
        [key for _, key, _ in requests],
        offline=config.offline,
    )
    for candidate, key, payload in requests:
        entry = entries.get(key)
        pair_id = str(payload["pair_id"])
        if entry is None:
            missing.append((candidate, key, payload))
        else:
            cached[pair_id] = entry
            state.add_cached_usage(
                dict(entry.get("usage") or {}),
                _str_or_none(entry.get("usage_scope")),
                "classify",
            )

    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} relation classifications"
            )
        stage_client = inference.client
        close_stage_client = False
        if stage_client is None:
            stage_client = LocalStructuredClient(
                config.llm_base_url,
                allow_remote=config.allow_remote_inference,
            )
            close_stage_client = True
        results = _run_async(
            _run_inference_stage(
                stage_client,
                [item[2] for item in missing],
                lambda batch: _local_classify_pair(
                    stage_client,
                    batch[0],
                    config,
                ),
                concurrency=config.llm_concurrency,
                close_after=close_stage_client,
            )
        )
        missing_by_pair = {
            str(item[2]["pair_id"]): item
            for item in missing
        }
        pending_cache_writes: dict[str, dict[str, Any]] = {}
        for batch_items, result in results:
            by_pair: dict[str, dict[str, object]] = {}
            observed_model = config.classify_model
            usage: dict[str, int] = {}
            error: str | None = None
            error_state: CacheState = "stable_failure"
            error_type: str | None = None
            status_code: int | None = None
            if isinstance(result, Exception):
                error = str(result)
                error_state = (
                    "transient_failure"
                    if _is_transient_error(result)
                    else "stable_failure"
                )
                error_type = type(result).__name__
                raw_status = getattr(result, "status_code", None)
                status_code = raw_status if isinstance(raw_status, int) else None
            else:
                parsed, observed_model, usage = result
                by_pair = {
                    parsed.pair_id: parsed.model_dump(mode="json")
                }
                state.observed_classify_models.add(observed_model)
                state.add_usage(usage, "classify")
            usage_scope = cache.usage_scope("classify", batch_items)
            for payload in batch_items:
                pair_id = str(payload["pair_id"])
                _, key, _ = missing_by_pair[pair_id]
                pair_error = error
                parsed_pair = by_pair.get(pair_id)
                if parsed_pair is None and pair_error is None:
                    pair_error = "structured output omitted this pair"
                entry = cache_entry(
                    task="classify",
                    parsed=parsed_pair,
                    error=pair_error,
                    observed_model=observed_model,
                    usage=usage,
                    usage_scope=usage_scope,
                    state=(
                        error_state
                        if error is not None
                        else ("success" if parsed_pair is not None else "stable_failure")
                    ),
                    error_type=error_type,
                    status_code=status_code,
                )
                pending_cache_writes[key] = entry
                cached[pair_id] = entry
        cache.put_many(pending_cache_writes)

    edges: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for candidate in selected:
        pair_id = _pair_id(
            str(candidate["proposition_a_id"]),
            str(candidate["proposition_b_id"]),
        )
        entry = cached[pair_id]
        observed_model = str(entry.get("observed_model") or config.classify_model)
        state.observed_classify_models.add(observed_model)
        classification: AtomicPairAssessment | None = None
        error = cache_error(entry)
        if not error and entry.get("parsed") is not None:
            try:
                classification = AtomicPairAssessment.model_validate(entry["parsed"])
                if classification.pair_id != pair_id:
                    raise ValueError("classification returned the wrong pair_id")
            except (ValueError, TypeError) as exc:
                error = str(exc)
        if error or classification is None:
            candidate.update(
                {
                    "status": "review",
                    "requires_review": True,
                    "explanation": error or "missing classification",
                    "assumptions": [],
                    "discovery_method": "generative_model",
                    "model_version": observed_model,
                    "prompt_version": CLASSIFY_PROMPT_VERSION,
                    "inference_fingerprint": inference.fingerprints["classify"],
                    "model_profile_id": (
                        inference.profile.profile_id if inference.profile else None
                    ),
                }
            )
            reviews.append(
                _review_row(
                    "classification_error",
                    str(candidate["proposition_a_id"]),
                    str(candidate["proposition_b_id"]),
                    None,
                    None,
                    error or "missing classification",
                    [],
                    observed_model,
                    CLASSIFY_PROMPT_VERSION,
                )
            )
            continue

        relation, derivation_error = _derive_atomic_relation(classification)
        explanation = _atomic_explanation(classification, relation, derivation_error)
        candidate.update(
            {
                "classification_relation": relation,
                "classification_confidence": classification.confidence,
                "supporting_fields": json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in classification.supporting_fields
                    ],
                    sort_keys=True,
                ),
                "atomic_a_implies_b": classification.a_implies_b,
                "atomic_b_implies_a": classification.b_implies_a,
                "atomic_can_both_be_true": classification.can_both_be_true,
                "atomic_must_one_be_true": classification.must_one_be_true,
                "atomic_logically_related": classification.logically_related,
                "a_implies_b": classification.a_implies_b == "yes",
                "b_implies_a": classification.b_implies_a == "yes",
                "explanation": explanation,
                "assumptions": classification.assumptions,
                "requires_review": classification.requires_review,
                "unsupported_assumption": classification.unsupported_assumption,
                "discovery_method": "generative_model",
                "model_version": observed_model,
                "prompt_version": CLASSIFY_PROMPT_VERSION,
                "inference_fingerprint": inference.fingerprints["classify"],
                "model_profile_id": (
                    inference.profile.profile_id if inference.profile else None
                ),
            }
        )
        validation_error = derivation_error or _classification_validation_error(
            classification,
            by_id[str(candidate["proposition_a_id"])],
            by_id[str(candidate["proposition_b_id"])],
        )
        if validation_error:
            candidate["status"] = "review"
            candidate["requires_review"] = True
            reviews.append(
                _review_row(
                    "classification_invalid_evidence",
                    str(candidate["proposition_a_id"]),
                    str(candidate["proposition_b_id"]),
                    relation,
                    classification.confidence,
                    validation_error,
                    classification.assumptions,
                    observed_model,
                    CLASSIFY_PROMPT_VERSION,
                )
            )
            continue
        assert relation is not None
        accepted_label = relation not in {"unrelated", "uncertain"}
        profile_relation = (
            "implies"
            if relation in {"A_implies_B", "B_implies_A"}
            else relation
        )
        calibrated = (
            inference.profile is not None
            and profile_relation in inference.profile.relations
            and inference.profile.relations[profile_relation].enabled
        )
        profile_threshold = (
            inference.profile.relations[profile_relation].threshold
            if calibrated and inference.profile is not None
            else 1.0
        )
        effective_threshold = max(
            config.threshold_for(relation),
            profile_threshold,
        )
        accepted = (
            accepted_label
            and classification.confidence
            >= effective_threshold
            and not classification.requires_review
            and not classification.unsupported_assumption
            and calibrated
        )
        if accepted:
            assert inference.profile is not None
            candidate["status"] = "accepted"
            relation_row = _classification_relation(
                candidate,
                classification,
                relation,
            )
            edges.append(
                _logic_edge_row(
                    relation_row,
                    by_id,
                    discovery_method="generative_model",
                    rule_version=None,
                    model_version=observed_model,
                    prompt_version=CLASSIFY_PROMPT_VERSION,
                    assumptions=classification.assumptions,
                    inference_fingerprint_value=inference.fingerprints["classify"],
                    model_profile_id=inference.profile.profile_id,
                )
            )
        elif relation == "unrelated" and not classification.requires_review:
            candidate["status"] = "rejected"
        else:
            candidate["status"] = "review"
            kind = (
                "uncalibrated_model"
                if accepted_label and not calibrated
                else (
                    "classification_unsupported_assumption"
                    if classification.unsupported_assumption
                    else (
                        "classification_requires_review"
                        if classification.requires_review
                        else "classification_low_confidence"
                    )
                )
            )
            reviews.append(
                _review_row(
                    kind,
                    str(candidate["proposition_a_id"]),
                    str(candidate["proposition_b_id"]),
                    relation,
                    classification.confidence,
                    explanation,
                    classification.assumptions,
                    observed_model,
                    CLASSIFY_PROMPT_VERSION,
                )
            )
    return AdjudicationStageResult(edges=edges, reviews=reviews)


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
) -> tuple[AtomicPairAssessment, str, dict[str, int]]:
    result = await _with_retries(
        lambda: client.generate(
            model=config.classify_model,
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
        "edge_basis": "generative_model_classifier",
        "explanation": _atomic_explanation(classification, relation, None),
        "confidence": classification.confidence,
    }


def _logic_edge_row(
    relation: dict[str, Any],
    propositions: dict[str, dict[str, Any]],
    *,
    discovery_method: str,
    rule_version: str | None,
    model_version: str | None,
    prompt_version: str | None,
    assumptions: list[str],
    inference_fingerprint_value: str | None = None,
    model_profile_id: str | None = None,
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
                str(model_version or ""),
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
        "model_version": model_version,
        "prompt_version": prompt_version,
        "explanation": explanation,
        "assumptions": assumptions,
        "rule_id": rule_id,
        "proposal_id": proposal_id,
        "solver_version": None,
        "constraint_version": None,
        "solver_component_id": None,
        "inference_fingerprint": (
            inference_fingerprint_value or src.get("inference_fingerprint")
        ),
        "model_profile_id": model_profile_id or src.get("model_profile_id"),
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
    reviews = [
        _review_row(
            "consistency_conflict",
            str(row["src_node_id"]),
            str(row["dst_node_id"]),
            str(row["edge_type"]),
            float(row["confidence"]),
            str(row["rejection_reason"]),
            [],
            _str_or_none(row.get("model_version")),
            _str_or_none(row.get("prompt_version")),
        )
        for row in rejected
    ]
    return SolvingStageResult(
        accepted_edges=accepted,
        rejected_edges=rejected,
        reviews=reviews,
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
        "parser_model",
        "prompt_version",
        "inference_fingerprint",
        "model_profile_id",
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


def _seed_classification_cache_from_incremental(
    cache: InferenceCache,
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    incremental_stats: IncrementalStats,
    resources: IncrementalResources,
    inference: _InferenceContext,
) -> None:
    prior = {
        (
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        ): row
        for row in resources.prior_classifications
    }
    prior_propositions = {
        str(row["proposition_id"]): row
        for row in resources.prior_propositions
    }
    unchanged_markets = set(
        resources.unchanged_market_ids
    )
    by_id = {
        str(row["proposition_id"]): row for row in propositions
    }
    schema_hash = _model_schema_hash(AtomicPairAssessment)
    prompt_hash = _text_hash(_CLASSIFY_PROMPT)
    pending: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        pair = (
            str(candidate["proposition_a_id"]),
            str(candidate["proposition_b_id"]),
        )
        row = prior.get(pair)
        if row is None or any(
            str(by_id[proposition_id]["market_id"]) not in unchanged_markets
            for proposition_id in pair
        ):
            continue
        if row.get("inference_fingerprint") != inference.fingerprints["classify"]:
            continue
        if not row.get("atomic_a_implies_b"):
            continue
        payload = _pair_payload(candidate, by_id)
        if any(
            proposition_id not in prior_propositions
            for proposition_id in pair
        ):
            continue
        prior_payload = _pair_payload(row, prior_propositions)
        if json.dumps(
            prior_payload,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ) != json.dumps(
            payload,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ):
            continue
        key = cache.key(
            "classify",
            inference.fingerprints["classify"],
            CLASSIFY_PROMPT_VERSION,
            prompt_hash,
            schema_hash,
            payload,
        )
        supporting_fields = json.loads(row.get("supporting_fields") or "[]")
        assumptions = list(row.get("assumptions") or [])
        parsed = AtomicPairAssessment.model_validate(
            {
                "pair_id": str(payload["pair_id"]),
                "confidence": float(row["classification_confidence"]),
                "supporting_fields": supporting_fields,
                "assumptions": assumptions,
                "a_implies_b": str(row["atomic_a_implies_b"]),
                "b_implies_a": str(row["atomic_b_implies_a"]),
                "can_both_be_true": str(row["atomic_can_both_be_true"]),
                "must_one_be_true": str(row["atomic_must_one_be_true"]),
                "logically_related": str(row["atomic_logically_related"]),
                "unsupported_assumption": bool(
                    row["unsupported_assumption"]
                ),
                "requires_review": bool(row["requires_review"]),
            }
        )
        pending[key] = cache_entry(
            task="classify",
            parsed=parsed.model_dump(mode="json"),
            error=None,
            observed_model=str(
                row.get("model_version") or config.classify_model
            ),
            usage={},
            usage_scope=None,
            state="success",
        )
    existing = cache.contains_many(tuple(pending))
    writes = {
        key: entry for key, entry in pending.items() if key not in existing
    }
    cache.put_many(writes)
    seeded = len(writes)
    incremental_stats["baseline_classification_entries_seeded"] = (
        incremental_stats.get("baseline_classification_entries_seeded", 0)
        + seeded
    )


def _stage_workspace_tables(
    candidates: CandidateStore,
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
    logic_edges: Sequence[dict[str, Any]],
    rejected_edges: Sequence[dict[str, Any]],
    parse_errors: Sequence[dict[str, Any]],
    reviews_: Sequence[dict[str, Any]],
    proposition_fingerprint_state: Sequence[dict[str, Any]],
    candidate_component_state: Sequence[dict[str, Any]],
    solver_component_state: Sequence[dict[str, Any]],
    execution_plan_rows: Sequence[dict[str, Any]],
) -> None:
    db = candidates.db
    parser_by_market = {
        str(row["market_id"]): str(row["parser_model"])
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
    _create_and_fill(db, "review_queue_v", REVIEW_COLUMNS, reviews_)
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
    reviews_: Sequence[dict[str, Any]],
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
            "review_queue_v",
            directory / "review_queue.parquet",
            list(REVIEW_COLUMNS),
            "review_id",
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
                    "WHERE discovery_method IN ('generative_model', 'nli')"
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
                in {"generative_model", "nli"}
            ),
            "conditional_edges": int(
                db.scalar("SELECT count(*) FROM conditional_edges_v") or 0
            ),
            "review_queue": len(reviews_),
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
            directory / "model_manifest.json",
            inference.manifest.model_dump(mode="json"),
        )
        if inference.profile is not None:
            _write_json_atomic(
                directory / "model_profile.json",
                inference.profile.model_dump(mode="json"),
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
        "review_queue.parquet": REVIEW_COLUMNS,
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
        "model_manifest.json",
        *(("model_profile.json",) if inference.profile is not None else ()),
        *(("compute_profile.json",) if config.compute_profile is not None else ()),
        *(
            ("benchmark.parquet", "evaluation_report.json")
            if config.benchmark_path is not None
            else ()
        ),
    ]
    manifest = {
        "command": "discover",
        "version": __version__,
        "input": str(input_path),
        "input_hash": input_hash,
        "input_schema": source_schema,
        "models": {
            "parse": {
                "requested": config.parse_model,
                "observed": sorted(state.observed_parse_models),
            },
            "classify": {
                "requested": config.classify_model,
                "observed": sorted(state.observed_classify_models),
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
            "origin": inference.manifest.inference_origin,
            "runtime": inference.manifest.runtime,
            "runtime_version": inference.manifest.runtime_version,
            "model_manifest_id": inference.manifest.manifest_id,
            "model_manifest_hash": manifest_sha256(inference.manifest),
            "model_profile_id": (
                inference.profile.profile_id if inference.profile else None
            ),
            "profiled": inference.profile is not None,
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
            "allow_unbenchmarked_rules": config.allow_unbenchmarked_rules,
        },
        "incremental": {
            "baseline": (
                str(config.incremental_from.resolve())
                if config.incremental_from is not None
                else None
            ),
        },
        "benchmark": (
            {
                "path": str(config.benchmark_path.resolve()),
                "hash": _sha256(config.benchmark_path.resolve()),
            }
            if config.benchmark_path is not None
            else None
        ),
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


def _dedupe_reviews(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        {str(row["review_id"]): row for row in rows}.values(),
        key=lambda row: str(row["review_id"]),
    )


def _parse_error_rows(
    propositions: Sequence[dict[str, Any]],
    reviews: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    review_by_id = {
        str(row["proposition_a_id"]): row
        for row in reviews
        if str(row.get("review_kind") or "").startswith("parse_")
    }
    rows = []
    for proposition in propositions:
        proposition_id = str(proposition["proposition_id"])
        review = review_by_id.get(proposition_id)
        if review is None:
            continue
        kind = str(review["review_kind"])
        rows.append(
            {
                "error_id": _text_hash(
                    f"{proposition_id}|{kind}|{review.get('explanation') or ''}"
                ),
                "proposition_id": proposition_id,
                "market_id": proposition["market_id"],
                "error_kind": kind,
                "error_message": review.get("explanation") or "",
                "cache_state": proposition.get("_parse_cache_state"),
                "error_type": proposition.get("_parse_error_type"),
                "status_code": proposition.get("_parse_status_code"),
                "response_json": proposition.get("_parse_response_json"),
                "question": proposition["question"],
                "description": proposition["description"],
                "parse_confidence": proposition["parse_confidence"],
                "market_source_hash": proposition["market_source_hash"],
                "parser_model": proposition["parser_model"],
                "prompt_version": proposition["prompt_version"],
                "schema_version": _model_schema_hash(ParsedMarket),
                "normalization_version": proposition["normalization_version"],
            }
        )
    return sorted(rows, key=lambda row: str(row["error_id"]))


def _review_row(
    kind: str,
    proposition_a_id: str,
    proposition_b_id: str | None,
    relation: str | None,
    confidence: float | None,
    explanation: str,
    assumptions: list[str],
    model_version: str | None,
    prompt_version: str | None,
) -> dict[str, Any]:
    review_id = _text_hash(
        "|".join(
            (
                kind,
                proposition_a_id,
                proposition_b_id or "",
                relation or "",
                explanation,
            )
        )
    )
    return {
        "review_id": review_id,
        "proposition_a_id": proposition_a_id,
        "proposition_b_id": proposition_b_id,
        "review_kind": kind,
        "proposed_relation": relation,
        "confidence": confidence,
        "explanation": explanation,
        "assumptions": assumptions,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }


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
            except Exception as exc:  # preserve failures as auditable review rows
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
        return await _run_batched(
            payloads,
            1,
            concurrency,
            call,
        )
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


def _run_async(
    awaitable: Coroutine[Any, Any, _AsyncResult],
) -> _AsyncResult:
    return asyncio.run(awaitable)
