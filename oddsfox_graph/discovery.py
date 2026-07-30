from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover - exercised by CLI installation error
    raise ImportError(
        'Automated discovery requires `pip install -e ".[discovery]"`.'
    ) from exc

from ._diagnostic_stages import write_conditionals
from ._discovery.bulk import create_and_fill as _create_and_fill
from ._discovery.cache import (
    CACHE_ENTRY_VERSION,
    JsonCache,
    cache_entry,
    cache_error,
)
from ._discovery.candidates import (
    candidate_sort_key as _candidate_sort_key,
    generate_candidate_store as _generate_candidate_store_bounded,
    generate_candidates as _generate_candidates_bounded,
    structural_member_limit as _structural_member_limit,
)
from ._discovery.contracts import (
    DEFAULT_EMBEDDING_REVISION,
    DiscoveryConfig,
    PairClassification,
    PairClassificationBatch,
    ParsedMarket,
    ParsedMarketBatch,
    ParsedOutcome,
    PropositionRecord,
    SourceMarket,
    SourceOutcome,
)
from ._discovery.input import (
    datetime_or_none as _datetime_or_none,
    load_source_markets as _load_source_markets,
    str_or_none as _str_or_none,
    utc_datetime as _utc_datetime,
)
from ._discovery.metrics import RunState, StageRecorder
from ._discovery.incremental import EXECUTION_PLAN_COLUMNS, ExecutionPlan
from ._discovery.publication import copy_sorted_parquet as _copy_table
from ._discovery.types import IncrementalStats
from ._discovery.relations import (
    HARD_FACT_RULE_IDS,
    RULE_REGISTRY,
    SEMANTIC_KEYS as _SEMANTIC_KEYS,
    deterministic_relation as _deterministic_relation,
    hashable as _hashable,
    is_winner_proposition as _is_winner_proposition,
    normalize_text as _normalize_text,
    proposition_signature as _proposition_signature,
    stage_rank as _stage_rank,
)
from ._discovery.solver import (
    CONSTRAINT_VERSION,
    SOLVER_VERSION,
    proposal_set_hash,
    solve_proposals,
)
from ._discovery.versions import (
    CANDIDATE_STATE_VERSION,
    CLASSIFY_PROMPT_VERSION,
    DOMAIN_TAXONOMY_VERSION,
    NORMALIZATION_VERSION,
    PARSE_PROMPT_VERSION,
    PUBLICATION_VERSION,
    RETRIEVAL_VERSION,
    RULE_VERSION,
    EXECUTION_PLAN_VERSION,
)
from ._discovery.workspace import (
    CANDIDATE_BLOCK_COLUMNS,
    CANDIDATE_COLUMNS,
    CANDIDATE_REASON_COLUMNS,
    CandidateStore,
)
from . import __version__
from .artifacts import ARTIFACT_COLUMNS, REPORTS, reports
from .graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT, write_graph_snapshot
from .queries import DuckDB, q
from .reports import write_reports, write_summary_report


__all__ = [
    "DEFAULT_EMBEDDING_REVISION",
    "DiscoveryConfig",
    "JsonCache",
    "PairClassification",
    "PairClassificationBatch",
    "ParsedMarket",
    "ParsedMarketBatch",
    "ParsedOutcome",
    "PropositionRecord",
    "SourceMarket",
    "SourceOutcome",
    "discover",
    "_datetime_or_none",
    "_load_source_markets",
]


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
    "source_format": "VARCHAR",
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

EMBEDDING_STATE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "text_hash": "VARCHAR",
    "embedding_model": "VARCHAR",
    "embedding_revision": "VARCHAR",
    "embedding": "FLOAT[]",
}

SEMANTIC_NEIGHBOR_STATE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "neighbor_id": "VARCHAR",
    "similarity": "DOUBLE",
    "neighbor_rank": "INTEGER",
    "proposition_text_hash": "VARCHAR",
    "neighbor_text_hash": "VARCHAR",
    "embedding_model": "VARCHAR",
    "embedding_revision": "VARCHAR",
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
        ),
        strict=True,
    )
)


_ENTITY_ALIASES = {
    "argentina national team": "Argentina",
    "btc": "Bitcoin",
    "bitcoin": "Bitcoin",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "us": "United States",
    "usa": "United States",
    "united states of america": "United States",
}

_UNIT_ALIASES = {
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "percent": "percent",
    "percentage": "percent",
    "usd": "USD",
    "us dollars": "USD",
    "%": "percent",
}

_PARSE_PROMPT = """Extract one proposition for every supplied market outcome.
Use the question, full description, outcome, and authoritative metadata only.
Use the outcome string exactly as supplied. Normalize dates, numbers, and units.
Use null for information that is absent or not supported by the schema; never invent it.
For Yes/No markets, set No to negative polarity. Return every market and outcome exactly once."""

_CLASSIFY_PROMPT = """Classify each proposition pair using only the supplied facts.
Allowed relations are equivalent, A_implies_B, B_implies_A, mutually_exclusive,
complement, compatible, unrelated, and uncertain. Evaluate A implies B and B
implies A independently before choosing the relation. Cite supporting fields
with their supplied values and state assumptions explicitly.
Use uncertain and requires_review=true whenever the relation depends on missing context.
Return every pair_id exactly once."""


def discover(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig | None = None,
    _client: object | None = None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None = None,
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

    source_format, input_rows, markets, input_selection = recorder.run(
        "normalize_input",
        lambda: _load_source_markets(
            input_path,
            max_propositions=config.max_propositions,
        ),
    )
    cache_dir = (config.cache_dir or Path(str(out_dir) + ".cache")).resolve()
    cache = JsonCache(cache_dir)
    baseline_embeddings, reusable_solver_components, incremental_stats = recorder.run(
        "prepare_incremental",
        lambda: _prepare_incremental(
            config,
            out_dir,
            markets,
            cache,
        ),
    )
    baseline_neighbors = incremental_stats.pop(
        "_baseline_semantic_neighbors",
        [],
    )
    prior_market_hashes = incremental_stats.pop("_prior_market_hashes", {})
    prior_solver_hashes = set(
        incremental_stats.pop("_prior_solver_hashes", [])
    )
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
    propositions, parse_reviews = recorder.run(
        "parse_propositions",
        lambda: _parse_propositions(
            markets,
            source_format,
            config,
            cache,
            state,
            _client,
        ),
    )
    rule_support = recorder.run(
        "benchmark_rule_gates",
        lambda: _apply_benchmark_rule_gates(
            propositions,
            config,
        ),
    )
    enabled_rule_ids = set(rule_support["enabled"])
    reusable_candidates_path = incremental_stats.pop(
        "_reusable_candidates_path",
        None,
    )
    prior_enabled_rules = set(
        incremental_stats.pop("_prior_enabled_rules", [])
    )
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
    baseline_candidate_blocks = incremental_stats.pop(
        "_baseline_candidate_blocks",
        None,
    )
    baseline_candidate_reasons = incremental_stats.pop(
        "_baseline_candidate_reasons",
        None,
    )
    embedding_state: list[dict[str, Any]] = []
    semantic_neighbor_state: list[dict[str, Any]] = []
    semantic_execution: list[dict[str, Any]] = []
    if reusable_candidates_path is not None:
        candidate_store = recorder.run(
            "generate_candidates",
            lambda: CandidateStore.from_parquet(
                Path(reusable_candidates_path),
                block_path=Path(str(baseline_candidate_blocks)),
                reason_path=Path(str(baseline_candidate_reasons)),
            ),
        )
        candidate_store.reset_for_run()
        candidate_store.structural_member_limit = _structural_member_limit(
            config.max_candidates
        )
        embedding_state.extend(
            _reused_embedding_state(
                propositions,
                baseline_embeddings,
                config,
            )
        )
        semantic_neighbor_state.extend(baseline_neighbors)
        semantic_execution.extend(
            {
                "proposition_id": str(row["proposition_id"]),
                "status": "reused",
            }
            for row in propositions
        )
        incremental_stats["candidate_generation_reused"] = True
    else:
        candidate_store = recorder.run(
            "generate_candidates",
            lambda: _generate_candidate_store(
                propositions,
                config,
                _embedder or _embed_texts,
                baseline_embeddings=baseline_embeddings,
                baseline_neighbors=baseline_neighbors,
                embedding_state_sink=embedding_state,
                neighbor_state_sink=semantic_neighbor_state,
                neighborhood_execution_sink=semantic_execution,
                baseline_candidate_blocks=baseline_candidate_blocks,
                baseline_candidate_reasons=baseline_candidate_reasons,
                enabled_rule_ids=enabled_rule_ids,
            ),
        )
        incremental_stats["candidate_generation_reused"] = False
    block_execution = candidate_store.block_execution_rows()
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
    _record_semantic_neighborhood_reuse(
        incremental_stats,
        baseline_neighbors,
        semantic_neighbor_state,
        execution_plan,
        semantic_execution,
    )
    if isinstance(baseline_neighbors, list):
        baseline_neighbors.clear()
    proposition_fingerprint_state = _proposition_fingerprint_rows(propositions)
    candidate_component_state = candidate_store.component_rows(
        sorted(str(row["proposition_id"]) for row in propositions),
        CANDIDATE_STATE_VERSION,
    )
    _record_candidate_component_reuse(
        incremental_stats,
        candidate_component_state,
    )
    deterministic_candidates = candidate_store.deterministic_rows()
    _hydrate_deterministic_candidates(
        deterministic_candidates,
        propositions,
        config,
    )
    candidate_store.update_rows(deterministic_candidates)
    deterministic_edges = recorder.run(
        "derive_deterministic_relations",
        lambda: _derive_deterministic_edges(
            deterministic_candidates,
            propositions,
        ),
    )
    candidate_store.mark_classification_budget()
    classification_candidates = candidate_store.classification_rows(
        config.max_llm_pairs
    )
    _seed_classification_cache_from_incremental(
        cache,
        classification_candidates,
        propositions,
        config,
        incremental_stats,
    )
    llm_edges, llm_reviews = recorder.run(
        "classify_pairs",
        lambda: _classify_candidates(
            classification_candidates,
            propositions,
            config,
            cache,
            state,
            _client,
        ),
    )
    candidate_store.update_rows(classification_candidates)
    logic_edges, rejected_edges, consistency_reviews, solver_stats = recorder.run(
        "solve_consistency",
        lambda: _solve_logic_edges(
            deterministic_edges + llm_edges,
            reusable_solver_components=reusable_solver_components,
        ),
    )
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
    review_rows = _dedupe_reviews(parse_reviews + llm_reviews + consistency_reviews)
    parse_error_rows = _parse_error_rows(propositions, parse_reviews)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.discovery-", dir=out_dir.parent)
    )
    try:
        stats = recorder.run(
            "publish_artifacts",
            lambda: _write_discovery_artifacts(
                staging,
                markets,
                propositions,
                candidate_store,
                logic_edges,
                rejected_edges,
                parse_error_rows,
                review_rows,
                source_format=source_format,
                input_rows=input_rows,
                input_selection=input_selection,
                solver_stats=solver_stats,
                rule_support=rule_support,
                embedding_state=embedding_state,
                semantic_neighbor_state=semantic_neighbor_state,
                proposition_fingerprint_state=proposition_fingerprint_state,
                candidate_component_state=candidate_component_state,
                solver_component_state=solver_component_state,
                execution_plan_rows=execution_plan.rows(),
                candidate_member_limit=int(
                    candidate_store.structural_member_limit or 0
                ),
                incremental_stats=incremental_stats,
            ),
        )
        input_hash = recorder.run("hash_input", lambda: _sha256(input_path))
        stats["runtime_seconds"] = recorder.runtime_seconds()
        stats["peak_rss_mb"] = _peak_rss_mb()
        evaluation: dict[str, Any] | None = None
        if config.benchmark_path is not None:
            from .evaluation import evaluate_build

            evaluation = recorder.run(
                "evaluate_benchmark",
                lambda: evaluate_build(
                    staging,
                    config.benchmark_path,
                    input_hash=input_hash,
                    pricing_file=config.pricing_file,
                    run_metadata={
                        "usage": state.usage_manifest(),
                        "models": {
                            "parse": {"requested": config.parse_model},
                            "classify": {"requested": config.classify_model},
                        },
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
        recorder.run("publish_files", lambda: _publish_staged(staging, out_dir))
        artifact_hashes = recorder.run(
            "hash_artifacts",
            lambda: {
                name: _sha256(out_dir / name)
                for name in (
                    *DISCOVERY_PARQUET_ARTIFACTS,
                    *(
                        ("benchmark.parquet",)
                        if (out_dir / "benchmark.parquet").is_file()
                        else ()
                    ),
                )
            },
        )
        state_hashes = recorder.run(
            "hash_incremental_state",
            lambda: {
                name: _sha256(out_dir / name) for name in STATE_ARTIFACTS
            },
        )
        stats["runtime_seconds"] = recorder.runtime_seconds()
        write_summary_report(out_dir, stats)
        manifest = _discovery_manifest(
            input_path,
            input_hash,
            artifact_hashes,
            source_format,
            stats,
            config,
            cache,
            state,
            recorder.timings,
            state_hashes,
        )
        _write_manifest_last(out_dir, manifest)
        if config.require_ready and (
            evaluation is None or evaluation["exit_decision"] != "READY_TO_SCALE"
        ):
            raise RuntimeError(
                "Discovery quality gates did not produce READY_TO_SCALE"
            )
        return dict(manifest["stats"])
    finally:
        candidate_store.close()
        shutil.rmtree(staging, ignore_errors=True)


def _with_packaged_benchmark(
    config: DiscoveryConfig,
    input_path: Path,
) -> DiscoveryConfig:
    if config.benchmark_path is not None:
        return config
    packaged = Path(__file__).parent / "benchmarks" / "v0.4.0.parquet"
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
    cache: JsonCache,
) -> tuple[
    dict[str, list[float]],
    dict[str, dict[str, Any]],
    IncrementalStats,
]:
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
        return {}, {}, {
            "enabled": False,
            "baseline_manifest_hash": None,
            "markets_reused": 0,
            "markets_changed": len(markets),
            "markets_removed": 0,
            "baseline_parse_entries_seeded": 0,
            "invalidation_reasons": ["clean_run"],
        }
    if explicit_baseline is not None and baseline == out_dir:
        raise ValueError("--incremental-from must be distinct from --out")
    manifest_path = baseline / "build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            "Incremental baseline is incomplete; missing " + str(manifest_path)
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_versions = manifest.get("versions") or {}
    if (
        str(manifest.get("version") or "").split(".")[:2]
        != __version__.split(".")[:2]
        or baseline_versions.get("candidate_state") != CANDIDATE_STATE_VERSION
    ):
        raise ValueError(
            "Incremental baseline uses incompatible pre-v0.5 discovery state; "
            "run one clean v0.5 discover build and use that completed output "
            "as --incremental-from"
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
        prior_propositions = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(propositions_path)}')
            ORDER BY market_id, outcome_index
            """
        )
        embedding_rows = db.rows(
            f"""
            SELECT text_hash, embedding_model, embedding_revision, embedding
            FROM read_parquet('{q(embedding_state_path)}')
            ORDER BY proposition_id
            """
        )
        semantic_neighbor_rows = db.rows(
            f"""
            SELECT *
            FROM read_parquet('{q(semantic_neighbor_state_path)}')
            ORDER BY proposition_id, neighbor_rank
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
        prior_classifications = db.rows(
            f"""
            SELECT proposition_a_id, proposition_b_id,
                   candidate_reasons, embedding_similarity, embedding_rank,
                   classification_relation, classification_confidence,
                   supporting_fields, a_implies_b, b_implies_a,
                   explanation, assumptions, requires_review,
                   model_version, prompt_version
            FROM read_parquet('{q(candidates_path)}')
            WHERE classification_relation IS NOT NULL
            ORDER BY proposition_a_id, proposition_b_id
            """
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
    prompts = manifest.get("prompts") or {}
    parse_compatible = (
        ((models.get("parse") or {}).get("requested") == config.parse_model)
        and ((prompts.get("parse") or {}).get("version") == PARSE_PROMPT_VERSION)
        and (
            (prompts.get("parse") or {}).get("schema_hash")
            == _model_schema_hash(ParsedMarketBatch)
        )
    )
    seeded = 0
    if parse_compatible:
        seeded = _seed_parse_cache_from_baseline(
            cache,
            markets,
            prior_propositions,
            unchanged_market_ids,
            config,
        )

    embedding_manifest = models.get("embedding") or {}
    embedding_compatible = (
        embedding_manifest.get("model") == config.embedding_model
        and embedding_manifest.get("revision") == config.embedding_revision
    )
    baseline_embeddings = (
        {
            str(row["text_hash"]): [float(value) for value in row["embedding"]]
            for row in embedding_rows
            if row["embedding_model"] == config.embedding_model
            and row["embedding_revision"] == config.embedding_revision
        }
        if embedding_compatible
        else {}
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
    classify_manifest = models.get("classify") or {}
    classify_prompt = prompts.get("classify") or {}
    classification_compatible = (
        classify_manifest.get("requested") == config.classify_model
        and classify_prompt.get("version") == CLASSIFY_PROMPT_VERSION
        and classify_prompt.get("schema_hash")
        == _model_schema_hash(PairClassificationBatch)
    )
    if not classification_compatible:
        reasons.append("classifier_model_prompt_or_schema")
    rejected_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prior_rejected:
        rejected_by_component[str(row["solver_component_id"])].append(row)
    reusable_solver_components = {
        str(row["proposal_hash"]): {
            "accepted_proposal_ids": list(row["accepted_proposal_ids"] or []),
            "rejected_proposal_ids": list(row["rejected_proposal_ids"] or []),
            "rejected_rows": rejected_by_component.get(
                str(row["solver_component_id"]),
                [],
            ),
            "hard_clause_count": int(row["hard_clause_count"]),
            "soft_clause_count": int(row["soft_clause_count"]),
            "objective_cost": int(row["objective_cost"]),
        }
        for row in solver_state_rows
        if row["solver_version"] == SOLVER_VERSION
        and row["constraint_version"] == CONSTRAINT_VERSION
    }
    return baseline_embeddings, reusable_solver_components, {
        "enabled": explicit_baseline is not None,
        "offline_state_replay": offline_replay_baseline is not None,
        "baseline_manifest_hash": _sha256(manifest_path),
        "markets_reused": len(unchanged_market_ids) if parse_compatible else 0,
        "markets_changed": (
            len(changed_market_ids)
            if parse_compatible
            else len(current_hashes)
        ),
        "markets_removed": len(removed_market_ids),
        "baseline_parse_entries_seeded": seeded,
        "baseline_embedding_vectors_available": len(baseline_embeddings),
        "baseline_solver_components_available": len(reusable_solver_components),
        "_prior_candidate_components": prior_candidate_components,
        "_prior_market_hashes": prior_markets,
        "_prior_solver_hashes": sorted(
            reusable_solver_components
        ),
        "_baseline_semantic_neighbors": (
            semantic_neighbor_rows
            if embedding_compatible
            and versions.get("retrieval") == RETRIEVAL_VERSION
            else []
        ),
        "_prior_classifications": (
            prior_classifications if classification_compatible else []
        ),
        "_prior_enabled_rules": list(
            (manifest.get("rules") or {}).get("enabled") or []
        ),
        "_prior_propositions": prior_propositions,
        "_unchanged_market_ids": sorted(unchanged_market_ids),
        "_reusable_candidates_path": (
            str(candidates_path) if candidate_compatible else None
        ),
        "_baseline_candidate_blocks": (
            str(candidate_blocks_path)
            if previous_limits.get("max_candidates") == config.max_candidates
            else None
        ),
        "_baseline_candidate_reasons": (
            str(candidate_reasons_path)
            if previous_limits.get("max_candidates") == config.max_candidates
            else None
        ),
        "invalidation_reasons": sorted(set(reasons)) or ["none"],
    }


def _seed_parse_cache_from_baseline(
    cache: JsonCache,
    markets: Sequence[SourceMarket],
    prior_propositions: Sequence[dict[str, Any]],
    unchanged_market_ids: set[str],
    config: DiscoveryConfig,
) -> int:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prior_propositions:
        market_id = str(row["market_id"])
        if market_id in unchanged_market_ids:
            by_market[market_id].append(row)
    schema_hash = _model_schema_hash(ParsedMarketBatch)
    seeded = 0
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
            config.parse_model,
            PARSE_PROMPT_VERSION,
            _text_hash(_PARSE_PROMPT),
            schema_hash,
            payload,
        )
        if (cache.directory / f"{key}.json").is_file():
            continue
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
                    polarity=str(row["polarity"]),
                    parse_confidence=float(row["parse_confidence"]),
                )
                for row in rows
            ],
        )
        cache.put(
            key,
            cache_entry(
                task="parse",
                parsed=parsed.model_dump(mode="json"),
                error=None,
                observed_model=str(rows[0]["parser_model"]),
                usage={},
                usage_scope=None,
                state="success",
            ),
        )
        seeded += 1
    return seeded


def _parse_propositions(
    markets: Sequence[SourceMarket],
    source_format: str,
    config: DiscoveryConfig,
    cache: JsonCache,
    state: RunState,
    client: object | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    schema_hash = _model_schema_hash(ParsedMarketBatch)
    cached: dict[str, dict[str, Any]] = {}
    missing: list[tuple[SourceMarket, str, dict[str, object]]] = []
    for market in markets:
        payload = _market_payload(market)
        key = cache.key(
            "parse",
            config.parse_model,
            PARSE_PROMPT_VERSION,
            _text_hash(_PARSE_PROMPT),
            schema_hash,
            payload,
        )
        entry = cache.get(key, offline=config.offline)
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
        client = client or _new_openai_client()
        missing_by_market = {
            item[0].market_id: item
            for item in missing
        }
        results = _run_async(
            _run_batched(
                [item[2] for item in missing],
                20,
                config.llm_concurrency,
                lambda batch: _openai_parse_batch(client, batch, config),
            )
        )
        for batch_items, result in results:
            by_id: dict[str, dict[str, object]] = {}
            observed_model = config.parse_model
            usage: dict[str, int] = {}
            error: str | None = None
            error_state = "stable_failure"
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
                    market.market_id: market.model_dump(mode="json")
                    for market in parsed.markets
                }
                state.observed_parse_models.add(observed_model)
                state.add_usage(usage, "parse")
            usage_scope = cache.usage_scope("parse", batch_items)
            for payload in batch_items:
                market_id = str(payload["market_id"])
                source_market, key, _ = missing_by_market[market_id]
                market_error = error
                parsed_market = by_id.get(market_id)
                if parsed_market is None and market_error is None:
                    market_error = "structured output omitted this market"
                entry = cache_entry(
                    task="parse",
                    parsed=parsed_market,
                    error=market_error,
                    observed_model=observed_model,
                    usage=usage,
                    usage_scope=usage_scope,
                    state=(
                        error_state
                        if error is not None
                        else ("success" if parsed_market is not None else "stable_failure")
                    ),
                    error_type=error_type,
                    status_code=status_code,
                )
                cache.put(key, entry)
                cached[source_market.market_id] = entry

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
                source_format,
                error,
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
    return sorted(propositions, key=lambda row: str(row["proposition_id"])), reviews


def _market_payload(market: SourceMarket) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "question": market.question,
        "description": market.description,
        "market_source_hash": market.source_hash,
        "event_id": market.event_id,
        "event_slug": market.event_slug,
        "category": market.category,
        "tags": list(market.tags),
        "time_start": market.time_start,
        "time_end": market.time_end,
        "outcomes": [
            {
                "outcome": outcome.outcome,
                "clob_token_id": outcome.clob_token_id,
            }
            for outcome in market.outcomes
        ],
    }


def _validate_parsed_market(source: SourceMarket, parsed: ParsedMarket) -> None:
    if parsed.market_id != source.market_id:
        raise ValueError(
            f"Structured parse returned market_id {parsed.market_id!r}; "
            f"expected {source.market_id!r}"
        )
    expected = [outcome.outcome for outcome in source.outcomes]
    actual = [outcome.outcome for outcome in parsed.propositions]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError(
            f"Structured parse outcomes for {source.market_id!r} do not match input"
        )


def _proposition_row(
    market: SourceMarket,
    source: SourceOutcome,
    parsed: ParsedOutcome | None,
    observed_model: str,
    source_format: str,
    error: str | None,
) -> dict[str, Any]:
    original_subject = parsed.subject if parsed else []
    object_original = parsed.object if parsed else None
    unit_original = parsed.unit if parsed else None
    competition_original = parsed.competition if parsed else None
    event_scope_original = parsed.event_scope if parsed else None
    jurisdiction_original = parsed.jurisdiction if parsed else None
    polarity = (
        parsed.polarity
        if parsed
        else ("negative" if source.outcome.casefold() == "no" else "positive")
    )
    return {
        "proposition_id": source.clob_token_id,
        "market_id": market.market_id,
        "event_id": market.event_id,
        "event_slug": market.event_slug,
        "clob_token_id": source.clob_token_id,
        "outcome_index": source.outcome_index,
        "outcome": source.outcome,
        "question": market.question,
        "description": market.description,
        "market_source_hash": market.source_hash,
        "normalization_version": NORMALIZATION_VERSION,
        "category": market.category,
        "tags": list(market.tags),
        "subject_original": original_subject,
        "subject": sorted(
            {
                _canonical_entity(subject)
                for subject in original_subject
                if _normalize_text(subject)
            }
        ),
        "predicate": _normalize_optional(parsed.predicate if parsed else None),
        "object_original": object_original,
        "object": _canonical_entity(object_original) if object_original else None,
        "operator": parsed.operator if parsed else None,
        "threshold": parsed.threshold if parsed else None,
        "unit_original": unit_original,
        "unit": _canonical_unit(unit_original) if unit_original else None,
        "time_start": _utc_datetime(
            (parsed.time_start if parsed else None) or market.time_start
        ),
        "time_end": _utc_datetime(
            (parsed.time_end if parsed else None) or market.time_end
        ),
        "competition_original": competition_original,
        "competition": (
            _canonical_entity(competition_original)
            if competition_original
            else None
        ),
        "event_scope_original": event_scope_original,
        "event_scope": (
            _canonical_entity(event_scope_original)
            if event_scope_original
            else None
        ),
        "jurisdiction_original": jurisdiction_original,
        "jurisdiction": (
            _canonical_entity(jurisdiction_original)
            if jurisdiction_original
            else None
        ),
        "polarity": polarity,
        "parse_confidence": parsed.parse_confidence if parsed and not error else 0.0,
        "parse_status": "parsed" if parsed and not error else "failed",
        "parser_model": observed_model,
        "prompt_version": PARSE_PROMPT_VERSION,
        "source_format": source_format,
        "_expected_tokens": len(market.outcomes),
        "_is_active": market.is_active,
        "_is_closed": market.is_closed,
        "_first_seen_ts": market.first_seen_ts or market.time_start,
        "_last_seen_ts": market.last_seen_ts or market.time_end,
    }


async def _openai_parse_batch(
    client: object,
    payloads: list[dict[str, object]],
    config: DiscoveryConfig,
) -> tuple[ParsedMarketBatch, str, dict[str, int]]:
    response = await _with_retries(
        lambda: client.responses.parse(
            model=config.parse_model,
            input=[
                {"role": "system", "content": _PARSE_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payloads, sort_keys=True, default=str),
                },
            ],
            text_format=ParsedMarketBatch,
            reasoning={"effort": "medium"},
            store=False,
        )
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise ValueError("OpenAI returned no parsed proposition output")
    parsed_batch = ParsedMarketBatch.model_validate(parsed)
    _validate_returned_ids(
        [str(payload["market_id"]) for payload in payloads],
        [market.market_id for market in parsed_batch.markets],
        "market",
    )
    return (
        parsed_batch,
        str(getattr(response, "model", config.parse_model)),
        _response_usage(response),
    )


def _generate_candidates(
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    embedder: Callable[[list[str], DiscoveryConfig], Any],
    *,
    baseline_embeddings: dict[str, list[float]] | None = None,
    baseline_neighbors: Sequence[dict[str, Any]] | None = None,
    embedding_state_sink: list[dict[str, Any]] | None = None,
    neighbor_state_sink: list[dict[str, Any]] | None = None,
    neighborhood_execution_sink: list[dict[str, Any]] | None = None,
    enabled_rule_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    return _generate_candidates_bounded(
        propositions,
        config,
        embedder,
        semantic_keys=_SEMANTIC_KEYS,
        hashable=_hashable,
        proposition_signature=_proposition_signature,
        deterministic_relation=_deterministic_relation,
        embedding_text=_embedding_text,
        stage_rank=_stage_rank,
        is_winner=_is_winner_proposition,
        baseline_embeddings=baseline_embeddings,
        baseline_neighbors=baseline_neighbors,
        embedding_state_sink=embedding_state_sink,
        neighbor_state_sink=neighbor_state_sink,
        neighborhood_execution_sink=neighborhood_execution_sink,
        enabled_rule_ids=enabled_rule_ids,
    )


def _generate_candidate_store(
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    embedder: Callable[[list[str], DiscoveryConfig], Any],
    *,
    baseline_embeddings: dict[str, list[float]] | None = None,
    baseline_neighbors: Sequence[dict[str, Any]] | None = None,
    embedding_state_sink: list[dict[str, Any]] | None = None,
    neighbor_state_sink: list[dict[str, Any]] | None = None,
    neighborhood_execution_sink: list[dict[str, Any]] | None = None,
    baseline_candidate_blocks: str | None = None,
    baseline_candidate_reasons: str | None = None,
    enabled_rule_ids: set[str] | None = None,
) -> CandidateStore:
    return _generate_candidate_store_bounded(
        propositions,
        config,
        embedder,
        semantic_keys=_SEMANTIC_KEYS,
        hashable=_hashable,
        proposition_signature=_proposition_signature,
        deterministic_relation=_deterministic_relation,
        embedding_text=_embedding_text,
        stage_rank=_stage_rank,
        is_winner=_is_winner_proposition,
        baseline_embeddings=baseline_embeddings,
        baseline_neighbors=baseline_neighbors,
        embedding_state_sink=embedding_state_sink,
        neighbor_state_sink=neighbor_state_sink,
        neighborhood_execution_sink=neighborhood_execution_sink,
        baseline_candidate_blocks=baseline_candidate_blocks,
        baseline_candidate_reasons=baseline_candidate_reasons,
        enabled_rule_ids=enabled_rule_ids,
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


def _reused_embedding_state(
    propositions: Sequence[dict[str, Any]],
    baseline_embeddings: dict[str, list[float]],
    config: DiscoveryConfig,
) -> list[dict[str, Any]]:
    rows = []
    for proposition in sorted(
        propositions,
        key=lambda row: str(row["proposition_id"]),
    ):
        text_hash = _text_hash(_embedding_text(proposition))
        embedding = baseline_embeddings.get(text_hash)
        if embedding is None:
            raise RuntimeError(
                "Reusable candidate state is missing an embedding vector"
            )
        rows.append(
            {
                "proposition_id": str(proposition["proposition_id"]),
                "text_hash": text_hash,
                "embedding_model": config.embedding_model,
                "embedding_revision": config.embedding_revision,
                "embedding": embedding,
                "reused": True,
            }
        )
    return rows


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
            'Automated discovery requires `pip install -e ".[discovery]"`.'
        ) from exc
    model = SentenceTransformer(
        config.embedding_model,
        revision=config.embedding_revision,
    )
    return model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def _embedding_text(proposition: dict[str, Any]) -> str:
    parts = [
        " ".join(proposition.get("subject") or []),
        proposition.get("predicate"),
        proposition.get("object"),
        proposition.get("operator"),
        proposition.get("threshold"),
        proposition.get("unit"),
        proposition.get("competition"),
        proposition.get("event_scope"),
        proposition.get("jurisdiction"),
        proposition.get("outcome"),
        proposition.get("question"),
        proposition.get("description"),
    ]
    return " | ".join(str(part) for part in parts if part not in (None, "", []))


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


def _classify_candidates(
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    cache: JsonCache,
    state: RunState,
    client: object | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    unresolved = sorted(
        (
            candidate
            for candidate in candidates
            if not candidate.get("_deterministic")
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

    schema_hash = _model_schema_hash(PairClassificationBatch)
    cached: dict[str, dict[str, Any]] = {}
    missing: list[tuple[dict[str, Any], str, dict[str, object]]] = []
    for candidate in selected:
        payload = _pair_payload(candidate, by_id)
        key = cache.key(
            "classify",
            config.classify_model,
            CLASSIFY_PROMPT_VERSION,
            _text_hash(_CLASSIFY_PROMPT),
            schema_hash,
            payload,
        )
        entry = cache.get(key, offline=config.offline)
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
        client = client or _new_openai_client()
        results = _run_async(
            _run_batched(
                [item[2] for item in missing],
                20,
                config.llm_concurrency,
                lambda batch: _openai_classify_batch(client, batch, config),
            )
        )
        missing_by_pair = {
            str(item[2]["pair_id"]): item
            for item in missing
        }
        for batch_items, result in results:
            by_pair: dict[str, dict[str, object]] = {}
            observed_model = config.classify_model
            usage: dict[str, int] = {}
            error: str | None = None
            error_state = "stable_failure"
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
                    pair.pair_id: pair.model_dump(mode="json")
                    for pair in parsed.pairs
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
                cache.put(key, entry)
                cached[pair_id] = entry

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
        classification: PairClassification | None = None
        error = cache_error(entry)
        if not error and entry.get("parsed") is not None:
            try:
                classification = PairClassification.model_validate(entry["parsed"])
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
                    "discovery_method": "llm",
                    "model_version": observed_model,
                    "prompt_version": CLASSIFY_PROMPT_VERSION,
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

        candidate.update(
            {
                "classification_relation": classification.relation,
                "classification_confidence": classification.confidence,
                "supporting_fields": json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in classification.supporting_fields
                    ],
                    sort_keys=True,
                ),
                "a_implies_b": classification.a_implies_b.supported,
                "b_implies_a": classification.b_implies_a.supported,
                "explanation": classification.explanation,
                "assumptions": classification.assumptions,
                "requires_review": classification.requires_review,
                "discovery_method": "llm",
                "model_version": observed_model,
                "prompt_version": CLASSIFY_PROMPT_VERSION,
            }
        )
        validation_error = _classification_validation_error(
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
                    classification.relation,
                    classification.confidence,
                    validation_error,
                    classification.assumptions,
                    observed_model,
                    CLASSIFY_PROMPT_VERSION,
                )
            )
            continue
        accepted_label = classification.relation not in {"unrelated", "uncertain"}
        accepted = (
            accepted_label
            and classification.confidence
            >= config.threshold_for(classification.relation)
            and not classification.requires_review
        )
        if accepted:
            candidate["status"] = "accepted"
            relation = _classification_relation(candidate, classification)
            edges.append(
                _logic_edge_row(
                    relation,
                    by_id,
                    discovery_method="llm",
                    rule_version=None,
                    model_version=observed_model,
                    prompt_version=CLASSIFY_PROMPT_VERSION,
                    assumptions=classification.assumptions,
                )
            )
        elif classification.relation == "unrelated" and not classification.requires_review:
            candidate["status"] = "rejected"
        else:
            candidate["status"] = "review"
            kind = (
                "classification_requires_review"
                if classification.requires_review
                else "classification_low_confidence"
            )
            reviews.append(
                _review_row(
                    kind,
                    str(candidate["proposition_a_id"]),
                    str(candidate["proposition_b_id"]),
                    classification.relation,
                    classification.confidence,
                    classification.explanation,
                    classification.assumptions,
                    observed_model,
                    CLASSIFY_PROMPT_VERSION,
                )
            )
    return edges, reviews


def _classification_validation_error(
    classification: PairClassification,
    proposition_a: dict[str, Any],
    proposition_b: dict[str, Any],
) -> str | None:
    expected_directions = {
        "equivalent": (True, True),
        "A_implies_B": (True, False),
        "B_implies_A": (False, True),
        "mutually_exclusive": (False, False),
        "complement": (False, False),
        "compatible": (False, False),
        "unrelated": (False, False),
        "uncertain": (False, False),
    }
    actual = (
        classification.a_implies_b.supported,
        classification.b_implies_a.supported,
    )
    if actual != expected_directions[classification.relation]:
        return (
            "directional entailment assessments disagree with the selected "
            f"relation {classification.relation}"
        )
    all_support = [
        *classification.supporting_fields,
        *classification.a_implies_b.supporting_fields,
        *classification.b_implies_a.supporting_fields,
    ]
    if classification.assumptions and not classification.supporting_fields:
        return "classification contains assumptions without supporting-field citations"
    for direction, assessment in (
        ("A-to-B", classification.a_implies_b),
        ("B-to-A", classification.b_implies_a),
    ):
        if assessment.assumptions and not assessment.supporting_fields:
            return (
                f"{direction} assessment contains assumptions without "
                "supporting-field citations"
            )
    if (
        classification.relation not in {"unrelated", "uncertain"}
        and not all_support
    ):
        return "positive classifications require supporting-field citations"
    for citation in all_support:
        proposition = (
            proposition_a if citation.proposition == "A" else proposition_b
        )
        raw_value = proposition.get(citation.field)
        if raw_value in (None, "", []):
            return (
                f"supporting field {citation.proposition}.{citation.field} "
                "is empty in the supplied proposition"
            )
        supplied = json.dumps(raw_value, sort_keys=True, default=str).casefold()
        if citation.value.strip().casefold() not in supplied:
            return (
                f"supporting value for {citation.proposition}.{citation.field} "
                "does not occur in the supplied proposition"
            )
    return None


async def _openai_classify_batch(
    client: object,
    payloads: list[dict[str, object]],
    config: DiscoveryConfig,
) -> tuple[PairClassificationBatch, str, dict[str, int]]:
    response = await _with_retries(
        lambda: client.responses.parse(
            model=config.classify_model,
            input=[
                {"role": "system", "content": _CLASSIFY_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payloads, sort_keys=True, default=str),
                },
            ],
            text_format=PairClassificationBatch,
            reasoning={"effort": "medium"},
            store=False,
        )
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise ValueError("OpenAI returned no parsed relation output")
    parsed_batch = PairClassificationBatch.model_validate(parsed)
    _validate_returned_ids(
        [str(payload["pair_id"]) for payload in payloads],
        [pair.pair_id for pair in parsed_batch.pairs],
        "pair",
    )
    return (
        parsed_batch,
        str(getattr(response, "model", config.classify_model)),
        _response_usage(response),
    )


def _pair_payload(
    candidate: dict[str, Any],
    propositions: dict[str, dict[str, Any]],
) -> dict[str, object]:
    a_id = str(candidate["proposition_a_id"])
    b_id = str(candidate["proposition_b_id"])
    return {
        "pair_id": _pair_id(a_id, b_id),
        "candidate_reasons": sorted(candidate["candidate_reasons"]),
        "proposition_A": _public_proposition(propositions[a_id]),
        "proposition_B": _public_proposition(propositions[b_id]),
    }


def _public_proposition(proposition: dict[str, Any]) -> dict[str, object]:
    public = {
        key: value
        for key, value in proposition.items()
        if not key.startswith("_")
    }
    return PropositionRecord.model_validate(public).model_dump()


def _classification_relation(
    candidate: dict[str, Any], classification: PairClassification
) -> dict[str, Any]:
    a_id = str(candidate["proposition_a_id"])
    b_id = str(candidate["proposition_b_id"])
    if classification.relation == "A_implies_B":
        edge_type, src, dst = "implies", a_id, b_id
    elif classification.relation == "B_implies_A":
        edge_type, src, dst = "implies", b_id, a_id
    else:
        edge_type = classification.relation
        src, dst = sorted((a_id, b_id))
    return {
        "edge_type": edge_type,
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_basis": "llm_classifier",
        "explanation": classification.explanation,
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
    }


def _solve_logic_edges(
    edges: Sequence[dict[str, Any]],
    *,
    reusable_solver_components: dict[str, dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
]:
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
    return accepted, rejected, reviews, stats


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


def _validate_logic_edges(
    edges: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    accepted: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for edge in edges:
        src, dst, edge_type = (
            str(edge["src_node_id"]),
            str(edge["dst_node_id"]),
            str(edge["edge_type"]),
        )
        if src == dst:
            raise RuntimeError(f"Logic edge cannot target itself: {src}")
        key = (src, dst, edge_type)
        if key in seen:
            raise RuntimeError(f"Duplicate logic edge: {key}")
        seen.add(key)
        if edge_type in {
            "complement",
            "equivalent",
            "mutually_exclusive",
            "compatible",
        } and src > dst:
            raise RuntimeError(f"Symmetric logic edge is not canonical: {key}")
        if edge["discovery_method"] == "embedding":
            raise RuntimeError("Embedding similarity cannot directly accept a logic edge")
        if not edge.get("explanation") or edge.get("assumptions") is None:
            raise RuntimeError(f"Logic edge lacks complete provenance: {key}")
        if edge["discovery_method"] == "deterministic" and not edge.get("rule_version"):
            raise RuntimeError(f"Deterministic edge lacks rule_version: {key}")
        if edge["discovery_method"] == "llm" and (
            not edge.get("model_version") or not edge.get("prompt_version")
        ):
            raise RuntimeError(f"LLM edge lacks model/prompt provenance: {key}")
        by_pair[tuple(sorted((src, dst)))].append(edge)

    for pair, pair_edges in sorted(by_pair.items()):
        relation_types = {str(edge["edge_type"]) for edge in pair_edges}
        if relation_types == {"complement", "mutually_exclusive"}:
            complements = [
                edge for edge in pair_edges if edge["edge_type"] == "complement"
            ]
            accepted.extend(complements)
            for edge in pair_edges:
                if (
                    edge["edge_type"] == "mutually_exclusive"
                    and edge["discovery_method"] == "llm"
                ):
                    reviews.append(
                        _review_row(
                            "consistency_conflict",
                            pair[0],
                            pair[1],
                            "mutually_exclusive",
                            float(edge["confidence"]),
                            "Complement subsumes the LLM exclusion relation",
                            list(edge["assumptions"]),
                            _str_or_none(edge.get("model_version")),
                            _str_or_none(edge.get("prompt_version")),
                        )
                    )
            continue
        if relation_types == {"implies"} and len(pair_edges) > 1:
            llm = [
                edge for edge in pair_edges if edge["discovery_method"] == "llm"
            ]
            deterministic = [
                edge
                for edge in pair_edges
                if edge["discovery_method"] == "deterministic"
            ]
            if len(deterministic) > 1:
                raise RuntimeError(
                    f"Conflicting deterministic implications for pair {pair}"
                )
            accepted.extend(deterministic)
            for edge in llm:
                reviews.append(
                    _review_row(
                        "consistency_conflict",
                        pair[0],
                        pair[1],
                        "implies",
                        float(edge["confidence"]),
                        "Opposite implications must be represented as one equivalence edge",
                        list(edge["assumptions"]),
                        _str_or_none(edge.get("model_version")),
                        _str_or_none(edge.get("prompt_version")),
                    )
                )
            continue
        if len(relation_types) == 1:
            accepted.extend(pair_edges)
            continue
        llm = [edge for edge in pair_edges if edge["discovery_method"] == "llm"]
        deterministic = [
            edge for edge in pair_edges if edge["discovery_method"] == "deterministic"
        ]
        if deterministic and llm:
            accepted.extend(deterministic)
            for edge in llm:
                reviews.append(
                    _review_row(
                        "consistency_conflict",
                        pair[0],
                        pair[1],
                        str(edge["edge_type"]),
                        float(edge["confidence"]),
                        "LLM relation conflicts with an accepted deterministic relation",
                        list(edge["assumptions"]),
                        _str_or_none(edge.get("model_version")),
                        _str_or_none(edge.get("prompt_version")),
                    )
                )
            continue
        if deterministic:
            raise RuntimeError(
                f"Conflicting deterministic relations for pair {pair}: "
                f"{sorted(relation_types)}"
            )
        for edge in llm:
            reviews.append(
                _review_row(
                    "consistency_conflict",
                    pair[0],
                    pair[1],
                    str(edge["edge_type"]),
                    float(edge["confidence"]),
                    "Conflicting LLM relations for one proposition pair",
                    list(edge["assumptions"]),
                    _str_or_none(edge.get("model_version")),
                    _str_or_none(edge.get("prompt_version")),
                )
            )
    return sorted(
        accepted,
        key=lambda edge: (
            str(edge["src_node_id"]),
            str(edge["dst_node_id"]),
            str(edge["edge_type"]),
        ),
    ), reviews


def _proposition_fingerprint_rows(
    propositions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    ignored = {
        "parse_confidence",
        "parse_status",
        "parser_model",
        "prompt_version",
        "source_format",
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
    incremental_stats: dict[str, Any],
    candidate_components: Sequence[dict[str, Any]],
) -> None:
    prior = incremental_stats.pop("_prior_candidate_components", {})
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
    incremental_stats: dict[str, Any],
    prior_rows: Sequence[dict[str, Any]],
    current_rows: Sequence[dict[str, Any]],
    execution_plan: ExecutionPlan | None = None,
    execution_rows: Sequence[dict[str, Any]] | None = None,
) -> None:
    def fingerprints(
        rows: Sequence[dict[str, Any]],
    ) -> dict[str, str]:
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
                json.dumps(sorted(neighbors), separators=(",", ":"))
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
    cache: JsonCache,
    candidates: Sequence[dict[str, Any]],
    propositions: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    incremental_stats: dict[str, Any],
) -> None:
    prior = {
        (
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        ): row
        for row in incremental_stats.pop("_prior_classifications", [])
    }
    prior_propositions = {
        str(row["proposition_id"]): row
        for row in incremental_stats.pop("_prior_propositions", [])
    }
    unchanged_markets = set(
        incremental_stats.pop("_unchanged_market_ids", [])
    )
    by_id = {
        str(row["proposition_id"]): row for row in propositions
    }
    schema_hash = _model_schema_hash(PairClassificationBatch)
    prompt_hash = _text_hash(_CLASSIFY_PROMPT)
    seeded = 0
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
        if (
            list(row.get("candidate_reasons") or [])
            != list(candidate.get("candidate_reasons") or [])
            or row.get("embedding_rank") != candidate.get("embedding_rank")
            or row.get("embedding_similarity")
            != candidate.get("embedding_similarity")
        ):
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
            config.classify_model,
            CLASSIFY_PROMPT_VERSION,
            prompt_hash,
            schema_hash,
            payload,
        )
        if (cache.directory / f"{key}.json").is_file():
            continue
        supporting_fields = json.loads(row.get("supporting_fields") or "[]")
        assumptions = list(row.get("assumptions") or [])
        parsed = PairClassification(
            pair_id=str(payload["pair_id"]),
            relation=str(row["classification_relation"]),
            confidence=float(row["classification_confidence"]),
            supporting_fields=supporting_fields,
            explanation=str(row["explanation"]),
            assumptions=assumptions,
            a_implies_b={
                "supported": bool(row["a_implies_b"]),
                "supporting_fields": (
                    supporting_fields if row["a_implies_b"] else []
                ),
                "assumptions": assumptions if row["a_implies_b"] else [],
            },
            b_implies_a={
                "supported": bool(row["b_implies_a"]),
                "supporting_fields": (
                    supporting_fields if row["b_implies_a"] else []
                ),
                "assumptions": assumptions if row["b_implies_a"] else [],
            },
            requires_review=bool(row["requires_review"]),
        )
        cache.put(
            key,
            cache_entry(
                task="classify",
                parsed=parsed.model_dump(mode="json"),
                error=None,
                observed_model=str(
                    row.get("model_version") or config.classify_model
                ),
                usage={},
                usage_scope=None,
                state="success",
            ),
        )
        seeded += 1
    incremental_stats["baseline_classification_entries_seeded"] = seeded


def _write_discovery_artifacts(
    directory: Path,
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
    candidates: CandidateStore,
    logic_edges: Sequence[dict[str, Any]],
    rejected_edges: Sequence[dict[str, Any]],
    parse_errors: Sequence[dict[str, Any]],
    reviews_: Sequence[dict[str, Any]],
    *,
    source_format: str,
    input_rows: int,
    input_selection: dict[str, object],
    solver_stats: dict[str, int],
    rule_support: dict[str, Any],
    embedding_state: Sequence[dict[str, Any]],
    semantic_neighbor_state: Sequence[dict[str, Any]],
    proposition_fingerprint_state: Sequence[dict[str, Any]],
    candidate_component_state: Sequence[dict[str, Any]],
    solver_component_state: Sequence[dict[str, Any]],
    execution_plan_rows: Sequence[dict[str, Any]],
    candidate_member_limit: int,
    incremental_stats: dict[str, Any],
) -> dict[str, object]:
    db = DuckDB(directory / "oddsfox_graph.duckdb")
    try:
        db.execute("SET TimeZone = 'UTC'")
        db.execute(
            f"SET temp_directory = '{q(directory / '.duckdb-spill')}'"
        )
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
        market_rows = _market_group_rows(markets, node_rows)
        _create_and_fill(
            db,
            "market_groups_v",
            MARKET_GROUP_COLUMNS,
            market_rows,
        )
        candidates.attach_to(db)
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
            "proposition_embeddings_v",
            EMBEDDING_STATE_COLUMNS,
            embedding_state,
        )
        _create_and_fill(
            db,
            "semantic_neighbors_v",
            SEMANTIC_NEIGHBOR_STATE_COLUMNS,
            semantic_neighbor_state,
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
        write_conditionals(db, directory)
        write_graph_snapshot(db, directory)
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
                "classified_pairs": "WHERE discovery_method = 'llm'",
                "unclassified_budget_pairs": (
                    "WHERE status = 'not_classified_budget'"
                ),
            }.items()
        }
        stats: dict[str, object] = {
            "input_rows": input_rows,
            "input_format": source_format,
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
            "llm_logic_edges": sum(
                1 for row in logic_edges if row.get("discovery_method") == "llm"
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
            },
            "incremental": {
                **incremental_stats,
                "embedding_vectors_reused": sum(
                    bool(row.get("reused")) for row in embedding_state
                ),
                "embedding_vectors_recomputed": sum(
                    not bool(row.get("reused")) for row in embedding_state
                ),
            },
            "time_range_start": min(
                (
                    market.first_seen_ts or market.time_start
                    for market in markets
                    if market.first_seen_ts or market.time_start
                ),
                default=None,
            ),
            "time_range_end": max(
                (
                    market.last_seen_ts or market.time_end
                    for market in markets
                    if market.last_seen_ts or market.time_end
                ),
                default=None,
            ),
        }
        write_reports(db, directory, stats)
        _validate_discovery_artifacts(db, directory)
        return stats
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
    source_format: str,
    stats: dict[str, object],
    config: DiscoveryConfig,
    cache: JsonCache,
    state: RunState,
    timings: dict[str, float],
    state_hashes: dict[str, str],
) -> dict[str, object]:
    artifact_names = [
        *DISCOVERY_PARQUET_ARTIFACTS,
        *STATE_ARTIFACTS,
        GRAPH_SNAPSHOT_ARTIFACT,
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
        "input_format": source_format,
        "input_granularity_seconds": (
            60 if source_format == "minutely" else (3600 if source_format == "hourly" else None)
        ),
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
        },
        "prompts": {
            "parse": {
                "version": PARSE_PROMPT_VERSION,
                "hash": _text_hash(_PARSE_PROMPT),
                "schema_hash": _model_schema_hash(ParsedMarketBatch),
            },
            "classify": {
                "version": CLASSIFY_PROMPT_VERSION,
                "hash": _text_hash(_CLASSIFY_PROMPT),
                "schema_hash": _model_schema_hash(PairClassificationBatch),
            },
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
        "pricing": (
            {
                "path": str(config.pricing_file.resolve()),
                "hash": _sha256(config.pricing_file.resolve()),
            }
            if config.pricing_file is not None
            else None
        ),
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
    }
    return json.loads(json.dumps(manifest, default=str))


def _publish_staged(staging: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "build_manifest.json"
    if manifest.exists():
        manifest.unlink()
    for name in DISCOVERY_PARQUET_ARTIFACTS:
        os.replace(staging / name, out_dir / name)
    state_out = out_dir / "state"
    state_out.mkdir(exist_ok=True)
    for relative_name in STATE_ARTIFACTS:
        name = Path(relative_name).name
        os.replace(staging / "state" / name, state_out / name)
    os.replace(staging / GRAPH_SNAPSHOT_ARTIFACT, out_dir / GRAPH_SNAPSHOT_ARTIFACT)
    reports_out = out_dir / "reports"
    reports_out.mkdir(exist_ok=True)
    for name in REPORTS:
        os.replace(staging / "reports" / name, reports_out / name)
    if (staging / "benchmark.parquet").exists():
        os.replace(staging / "benchmark.parquet", out_dir / "benchmark.parquet")
        os.replace(
            staging / "evaluation_report.json",
            out_dir / "evaluation_report.json",
        )
    else:
        for stale_name in ("benchmark.parquet", "evaluation_report.json"):
            stale = out_dir / stale_name
            if stale.exists():
                stale.unlink()


def _write_manifest_last(out_dir: Path, manifest: dict[str, object]) -> None:
    path = out_dir / "build_manifest.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".build_manifest.",
        suffix=".tmp",
        dir=out_dir,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


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
                "schema_version": _model_schema_hash(ParsedMarketBatch),
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


def _new_openai_client() -> object:
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(
            "OPENAI_API_KEY is required when discovery cache entries are missing"
        )
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise ImportError(
            'Automated discovery requires `pip install -e ".[discovery]"`.'
        ) from exc
    return AsyncOpenAI()


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
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (
        status_code in {408, 409, 429} or status_code >= 500
    ):
        return True
    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }


def _run_async(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


def _response_usage(response: object) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _model_schema_hash(model: type[BaseModel]) -> str:
    return _text_hash(
        json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
    )


def _validate_returned_ids(
    expected: Sequence[str],
    actual: Sequence[str],
    kind: str,
) -> None:
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError(
            f"Structured output {kind} IDs do not match the requested batch"
        )


def _pair_id(a_id: str, b_id: str) -> str:
    a_id, b_id = sorted((a_id, b_id))
    return _text_hash(f"{a_id}|{b_id}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_mb() -> float:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, ValueError):  # pragma: no cover - platform guard
        return 0.0
    # macOS reports bytes; Linux reports KiB.
    denominator = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(value / denominator, 3)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_text(value)
    return normalized or None


def _canonical_entity(value: str) -> str:
    normalized = _normalize_text(value)
    return _ENTITY_ALIASES.get(normalized.casefold(), normalized)


def _canonical_unit(value: str) -> str:
    normalized = _normalize_text(value)
    return _UNIT_ALIASES.get(normalized.casefold(), normalized)
