"""Complete-catalog deterministic discovery used by ``--mode fast``."""

from __future__ import annotations

import json
import gc
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from .bulk import create_and_fill, insert_rows
from .artifact_contracts import (
    CANDIDATE_COMPONENT_STATE_COLUMNS,
    DISCOVERY_JSON_ARTIFACTS,
    DISCOVERY_PARQUET_ARTIFACTS,
    GRAPH_DATABASE_ARTIFACT,
    LOGIC_EDGE_COLUMNS,
    MARKET_GROUP_COLUMNS,
    MARKET_STATE_COLUMNS,
    NODE_COLUMNS,
    PARSE_ERROR_COLUMNS,
    PROPOSITION_COLUMNS,
    PROPOSITION_FINGERPRINT_COLUMNS,
    QUALIFICATION_CASE_COLUMNS,
    REJECTED_EDGE_COLUMNS,
    SOLVER_COMPONENT_STATE_COLUMNS,
    STATE_ARTIFACTS,
)
from .consensus import MODEL_ASSESSMENT_COLUMNS, PARSE_ASSESSMENT_COLUMNS, QUARANTINE_COLUMNS
from .contracts import DiscoveryConfig, Operator, SourceMarket
from .extraction import ExtractedProposition, extract_proposition
from .incremental import EXECUTION_PLAN_COLUMNS
from .input import load_source_markets
from .metrics import StageRecorder
from .provenance import atomic_write_json, canonical_json_sha256, peak_rss_mb, sha256_file
from .publication import (
    copy_sorted_parquet,
    publish_directory_atomically,
    validate_source_output_paths,
    write_conditionals,
    write_manifest_last,
)
from .relations import RULE_REGISTRY, deterministic_relation
from .versions import (
    CANDIDATE_STATE_VERSION,
    CONSTRAINT_VERSION,
    EXECUTION_PLAN_VERSION,
    EXTRACTOR_ID,
    EXTRACTOR_VERSION,
    NORMALIZATION_VERSION,
    PUBLICATION_VERSION,
    RULE_VERSION,
    SOLVER_VERSION,
    SOURCE_SCHEMA,
    VIEWER_API_VERSION,
    VIEWER_ARTIFACT_VERSION,
    VISUALIZATION_LAYOUT_VERSION,
)
from .workspace import (
    CANDIDATE_BLOCK_COLUMNS,
    CANDIDATE_COLUMNS,
    CANDIDATE_REASON_COLUMNS,
    EMBEDDING_STATE_COLUMNS,
    SEMANTIC_NEIGHBOR_STATE_COLUMNS,
)
from .. import __version__
from .._explorer.aggregation import (
    COMPONENT_SUMMARY_COLUMNS,
    EVENT_RELATION_SUMMARY_COLUMNS,
    EVENT_SUMMARY_COLUMNS,
    NODE_METRIC_COLUMNS,
    VISUALIZATION_LAYOUT_COLUMNS,
    build_explorer_tables,
)
from ..artifacts import reports
from ..graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT, write_graph_snapshot
from ..queries import DuckDB, q
from ..reports import write_reports, write_summary_report
from .rule_qualification import qualify_rule_registry


_FAST_INTERNAL_COLUMNS = {
    **PROPOSITION_COLUMNS,
    "expected_tokens": "INTEGER",
    "is_active": "BOOLEAN",
    "is_closed": "BOOLEAN",
    "first_seen_ts": "TIMESTAMPTZ",
    "last_seen_ts": "TIMESTAMPTZ",
    "resolution_signature": "VARCHAR",
    "numeric_predicate_signature": "VARCHAR",
    "temporal_predicate_signature": "VARCHAR",
    "stage_family_signature": "VARCHAR",
    "winner_family_signature": "VARCHAR",
    "stage_rank": "INTEGER",
    "singular_winner": "BOOLEAN",
    "rule_applicability_fingerprint": "VARCHAR",
    "interval_low": "DOUBLE",
    "interval_low_inclusive": "BOOLEAN",
    "interval_high": "DOUBLE",
    "interval_high_inclusive": "BOOLEAN",
}

_STAGE_RANK = {
    "round of 32": 0,
    "round of 16": 1,
    "quarterfinal": 2,
    "semifinal": 3,
    "final": 4,
    "winner": 5,
}

_FORBIDDEN_FAST_IMPORT_PREFIXES = (
    "torch",
    "sentence_transformers",
    "usearch",
    "oddsfox_graph._discovery.cache",
    "oddsfox_graph._discovery.inference",
    "oddsfox_graph._discovery.nli",
    "oddsfox_graph._discovery.pipeline",
    "oddsfox_graph._discovery.retrieval",
)


def discover_fast(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig,
) -> dict[str, object]:
    """Build the complete precision-first graph without model dependencies."""

    initial_modules = frozenset(sys.modules)
    config.validate()
    if config.mode != "fast":
        raise ValueError("discover_fast requires mode=fast")
    _validate_fast_config(config)
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"Input parquet does not exist: {input_path}")
    validate_source_output_paths(input_path, out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    recorder = StageRecorder(config.progress_format)
    source_schema, input_rows, markets, selection = recorder.run(
        "normalize_input",
        lambda: load_source_markets(
            input_path,
            max_propositions=config.max_propositions,
        ),
    )
    baseline_manifest = _validate_incremental_baseline(config, input_path, out_dir)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.fast-", dir=out_dir.parent)
    )
    try:
        input_hash = sha256_file(input_path)
        can_reuse_unchanged = bool(
            baseline_manifest
            and baseline_manifest.get("input", {}).get("sha256") == input_hash
            and baseline_manifest.get("input", {}).get("selection") == selection
        )
        if (
            can_reuse_unchanged
            and config.incremental_from is not None
            and baseline_manifest is not None
        ):
            baseline_path = config.incremental_from.resolve()
            stats = recorder.run(
                "reuse_unchanged_fast_baseline",
                lambda: _reuse_unchanged_baseline(
                    baseline_path, staging, baseline_manifest
                ),
            )
        else:
            stats = recorder.run(
                "build_deterministic_workspace",
                lambda: _build_workspace(staging, markets, selection, config, recorder),
            )
            stats["incremental"] = {
                "enabled": baseline_manifest is not None,
                "unchanged_replay": False,
                "markets_reused": 0,
                "markets_recomputed": int(selection["selected_markets"]),
                "reason": (
                    "source_selection_changed"
                    if baseline_manifest is not None
                    else "clean_build"
                ),
            }
        stats.update(
            input_rows=input_rows,
            input_schema=source_schema,
            input_selection=selection,
            markets=int(selection["selected_markets"]),
            tokens=int(selection["selected_propositions"]),
            build_mode="fast",
            validation_status="DETERMINISTIC_VALIDATED",
        )
        loaded_in_fast = sorted(
            module
            for module in set(sys.modules) - initial_modules
            if module.startswith(_FORBIDDEN_FAST_IMPORT_PREFIXES)
        )
        if loaded_in_fast:
            raise RuntimeError(
                "Fast mode loaded forbidden inference resources: "
                + ", ".join(loaded_in_fast)
            )
        stats["inference_resources_loaded"] = loaded_in_fast
        stats["runtime_seconds"] = recorder.runtime_seconds()
        stats["peak_rss_mb"] = peak_rss_mb()
        stats["stage_metrics"] = recorder.stage_metrics
        deadline_met = recorder.runtime_seconds() <= config.deadline_seconds
        stats["deadline"] = {
            "seconds": config.deadline_seconds,
            "elapsed_seconds": recorder.runtime_seconds(),
            "met": deadline_met,
            "cutoff_triggered": False,
            "assessed_pairs": stats["candidate_edges"],
            "unassessed_pairs": 0,
        }
        write_summary_report(staging, stats)
        published_names = [
            *DISCOVERY_PARQUET_ARTIFACTS,
            *DISCOVERY_JSON_ARTIFACTS,
            GRAPH_DATABASE_ARTIFACT,
            GRAPH_SNAPSHOT_ARTIFACT,
            *reports(),
            *STATE_ARTIFACTS,
        ]
        artifact_hashes = {
            name: sha256_file(staging / name)
            for name in (*DISCOVERY_PARQUET_ARTIFACTS, *DISCOVERY_JSON_ARTIFACTS)
            if (staging / name).is_file()
        }
        state_hashes = {
            name: sha256_file(staging / name)
            for name in STATE_ARTIFACTS
            if (staging / name).is_file()
        }
        manifest = {
            "command": "discover",
            "version": __version__,
            "build_mode": "fast",
            "validation_status": "DETERMINISTIC_VALIDATED",
            "input": {
                "path": str(input_path),
                "sha256": input_hash,
                "schema": SOURCE_SCHEMA,
                "selection": selection,
            },
            "versions": {
                "publication": PUBLICATION_VERSION,
                "normalization": NORMALIZATION_VERSION,
                "extractor": EXTRACTOR_VERSION,
                "rules": RULE_VERSION,
                "candidate_state": CANDIDATE_STATE_VERSION,
                "execution_plan": EXECUTION_PLAN_VERSION,
                "solver": SOLVER_VERSION,
                "constraints": CONSTRAINT_VERSION,
            },
            "deadline": stats["deadline"],
            "stats": stats,
            "artifact_hashes": artifact_hashes,
            "state_hashes": state_hashes,
            "artifacts": sorted(
                name for name in published_names if (staging / name).is_file()
            ),
        }
        swap = recorder.run(
            "publish_files",
            lambda: publish_directory_atomically(staging, out_dir),
        )
        try:
            # The deadline covers a ready, manifest-complete graph, so decide it
            # only after the atomic directory publication has finished.
            elapsed_seconds = recorder.runtime_seconds()
            deadline_met = elapsed_seconds <= config.deadline_seconds
            stats["runtime_seconds"] = elapsed_seconds
            stats["peak_rss_mb"] = peak_rss_mb()
            stats["stage_metrics"] = recorder.stage_metrics
            stats["deadline"] = {
                "seconds": config.deadline_seconds,
                "elapsed_seconds": elapsed_seconds,
                "met": deadline_met,
                "cutoff_triggered": False,
                "assessed_pairs": stats["candidate_edges"],
                "unassessed_pairs": 0,
            }
            manifest["deadline"] = stats["deadline"]
            manifest["stats"] = stats
            write_summary_report(out_dir, stats)
            manifest["published_file_hashes"] = {
                name: sha256_file(out_dir / name) for name in sorted(published_names)
            }
            write_manifest_last(out_dir, manifest)
            ready_elapsed_seconds = recorder.runtime_seconds()
            if ready_elapsed_seconds > config.deadline_seconds and deadline_met:
                deadline_met = False
                stats["runtime_seconds"] = ready_elapsed_seconds
                stats["deadline"] = {
                    **stats["deadline"],
                    "elapsed_seconds": ready_elapsed_seconds,
                    "met": False,
                }
                manifest["deadline"] = stats["deadline"]
                manifest["stats"] = stats
                write_summary_report(out_dir, stats)
                manifest["published_file_hashes"] = {
                    name: sha256_file(out_dir / name)
                    for name in sorted(published_names)
                }
                write_manifest_last(out_dir, manifest)
        except Exception:
            swap.rollback()
            raise
        swap.finalize()
        recorder.event(
            "run_complete",
            build_mode="fast",
            runtime_seconds=recorder.runtime_seconds(),
            deadline_met=deadline_met,
            logic_edges=stats["logic_edges"],
        )
        return cast(dict[str, object], stats)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _validate_fast_config(config: DiscoveryConfig) -> None:
    forbidden: list[str] = []
    for name, value in (
        ("cache_dir", config.cache_dir),
        ("automation_profile", config.automation_profile),
        ("primary_model_manifest", config.primary_model_manifest),
        ("verifier_model_manifest", config.verifier_model_manifest),
        ("compute_profile", config.compute_profile),
    ):
        if value is not None:
            forbidden.append(name)
    if config.offline:
        forbidden.append("offline")
    if forbidden:
        raise ValueError(
            "Fast mode does not accept model, profile, cache, NLI, embedding, "
            "or offline options: " + ", ".join(sorted(forbidden))
        )


def _validate_incremental_baseline(
    config: DiscoveryConfig,
    input_path: Path,
    out_dir: Path,
) -> dict[str, Any] | None:
    if config.incremental_from is None:
        return None
    baseline = config.incremental_from.resolve()
    manifest_path = baseline / "build_manifest.json"
    if baseline == out_dir or baseline == input_path.parent or not manifest_path.is_file():
        raise ValueError("Incremental baseline is incomplete; run a clean v0.11 discovery")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Incremental baseline is incompatible; run a clean v0.11 discovery") from exc
    if manifest.get("version") != __version__ or manifest.get("build_mode") != "fast":
        raise ValueError("Incremental baseline is incompatible; run a clean v0.11 discovery")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or any(
        versions.get(key) != expected
        for key, expected in {
            "publication": PUBLICATION_VERSION,
            "normalization": NORMALIZATION_VERSION,
            "extractor": EXTRACTOR_VERSION,
            "rules": RULE_VERSION,
            "candidate_state": CANDIDATE_STATE_VERSION,
            "execution_plan": EXECUTION_PLAN_VERSION,
        }.items()
    ):
        raise ValueError("Incremental baseline is incompatible; run a clean v0.11 discovery")
    _validate_incremental_baseline_files(baseline, manifest)
    return {str(key): value for key, value in manifest.items()}


def _validate_incremental_baseline_files(
    baseline: Path,
    manifest: dict[str, Any],
) -> None:
    required_artifacts = {
        *DISCOVERY_PARQUET_ARTIFACTS,
        *DISCOVERY_JSON_ARTIFACTS,
        GRAPH_DATABASE_ARTIFACT,
        GRAPH_SNAPSHOT_ARTIFACT,
        *reports(),
        *STATE_ARTIFACTS,
    }
    declared = manifest.get("artifacts")
    if not isinstance(declared, list) or not required_artifacts <= {
        str(name) for name in declared
    }:
        raise ValueError(
            "Incremental baseline is incomplete; run a clean v0.11 discovery"
        )
    if any(not (baseline / name).is_file() for name in required_artifacts):
        raise ValueError(
            "Incremental baseline is incomplete; run a clean v0.11 discovery"
        )
    published_hashes = manifest.get("published_file_hashes")
    if not isinstance(published_hashes, dict) or set(published_hashes) != required_artifacts:
        raise ValueError(
            "Incremental baseline is incomplete; run a clean v0.11 discovery"
        )
    for name in sorted(required_artifacts):
        expected = published_hashes.get(name)
        if not isinstance(expected, str) or sha256_file(baseline / name) != expected:
            raise ValueError(
                "Incremental baseline artifact hashes do not match; "
                "run a clean v0.11 discovery"
            )
    for field, expected_names in (
        (
            "artifact_hashes",
            {*DISCOVERY_PARQUET_ARTIFACTS, *DISCOVERY_JSON_ARTIFACTS},
        ),
        ("state_hashes", set(STATE_ARTIFACTS)),
    ):
        hashes = manifest.get(field)
        if not isinstance(hashes, dict) or set(hashes) != expected_names:
            raise ValueError(
                "Incremental baseline is incomplete; run a clean v0.11 discovery"
            )
        for name in sorted(expected_names):
            expected = hashes.get(name)
            if not isinstance(expected, str) or published_hashes.get(name) != expected:
                raise ValueError(
                    "Incremental baseline artifact hashes do not match; "
                    "run a clean v0.11 discovery"
                )


def _reuse_unchanged_baseline(
    baseline: Path,
    staging: Path,
    manifest: dict[str, Any],
) -> dict[str, object]:
    shutil.copytree(baseline, staging, dirs_exist_ok=True)
    (staging / "build_manifest.json").unlink(missing_ok=True)
    prior_stats = manifest.get("stats")
    if not isinstance(prior_stats, dict):
        raise ValueError("Incremental baseline has no valid stats")
    stats = json.loads(json.dumps(prior_stats))
    selection = manifest.get("input", {}).get("selection", {})
    selected_markets = int(selection.get("selected_markets") or stats.get("markets") or 0)
    stats["incremental"] = {
        "enabled": True,
        "unchanged_replay": True,
        "markets_reused": selected_markets,
        "markets_recomputed": 0,
        "reason": "exact_input_and_contract_match",
    }
    return cast(dict[str, object], stats)


def _build_workspace(
    staging: Path,
    markets: list[SourceMarket],
    selection: dict[str, object],
    config: DiscoveryConfig,
    recorder: StageRecorder,
) -> dict[str, object]:
    database_path = staging / GRAPH_DATABASE_ARTIFACT
    db = DuckDB(database_path)
    try:
        checkpoint = time.perf_counter()
        fast_timings: dict[str, float] = {}

        def mark(name: str) -> None:
            nonlocal checkpoint
            now = time.perf_counter()
            fast_timings[name] = round(now - checkpoint, 3)
            checkpoint = now

        db.execute("SET TimeZone = 'UTC'")
        db.execute("SET memory_limit = '1024MB'")
        db.execute("SET threads = 2")
        db.execute("SET preserve_insertion_order = false")
        spill = staging / ".duckdb-spill"
        db.execute(f"SET temp_directory = '{q(spill)}'")
        create_and_fill(
            db,
            "fast_propositions",
            _FAST_INTERNAL_COLUMNS,
            [],
            temporary=True,
        )
        active_market_count = sum(market.is_active for market in markets)
        closed_market_count = sum(market.is_closed for market in markets)
        proposition_count = _insert_extracted_propositions(db, markets)
        markets.clear()
        gc.collect()
        mark("extract")
        recorder.event("fast_substage", stage="extract", rows=proposition_count)
        _create_public_base_tables(db)
        mark("base_tables")
        recorder.event("fast_substage", stage="base_tables")
        _create_fast_candidates(db, config.max_candidates)
        mark("candidates")
        recorder.event(
            "fast_substage",
            stage="candidates",
            rows=int(db.scalar("SELECT count(*) FROM relation_candidates_v") or 0),
        )
        _create_fast_logic_edges(db)
        _validate_deterministic_invariants(db)
        mark("relations")
        rule_support = recorder.run(
            "qualify_fast_rules",
            lambda: qualify_rule_registry(RULE_REGISTRY, deterministic_relation),
        )
        emitted_rule_ids = {
            str(row["rule_id"])
            for row in db.rows(
                "SELECT DISTINCT rule_id FROM logic_edges_v ORDER BY rule_id"
            )
        }
        disabled_emitted = emitted_rule_ids - set(rule_support["enabled"])
        if disabled_emitted:
            raise RuntimeError(
                "Fast publication attempted to use unqualified rules: "
                + ", ".join(sorted(disabled_emitted))
            )
        mark("rule_qualification")
        recorder.event(
            "fast_substage",
            stage="relations",
            rows=int(db.scalar("SELECT count(*) FROM logic_edges_v") or 0),
        )
        _create_empty_diagnostics(db)
        _create_incremental_state(db)
        mark("state")
        recorder.event("fast_substage", stage="state")
        coverage = build_explorer_tables(db, input_selection=selection)
        mark("explorer_aggregation")
        recorder.event("fast_substage", stage="explorer_aggregation")
        _export_artifacts(db, staging)
        write_conditionals(db, staging)
        write_graph_snapshot(db, staging)
        mark("artifact_export")
        recorder.event("fast_substage", stage="artifact_export")
        atomic_write_json(staging / "coverage_summary.json", coverage)
        _write_viewer_manifest(db, staging, coverage)
        mark("viewer_manifest")
        candidate_count = int(db.scalar("SELECT count(*) FROM relation_candidates_v") or 0)
        edge_count = int(db.scalar("SELECT count(*) FROM logic_edges_v") or 0)
        complement_count = int(
            db.scalar("SELECT count(*) FROM logic_edges_v WHERE edge_type='complement'") or 0
        )
        exclusion_count = int(
            db.scalar(
                "SELECT count(*) FROM logic_edges_v WHERE edge_type='mutually_exclusive' AND edge_basis='same_market'"
            )
            or 0
        )
        cross_market_count = int(
            db.scalar("SELECT count(*) FROM logic_edges_v WHERE market_id_src != market_id_dst") or 0
        )
        cross_event_count = int(
            db.scalar(
                "SELECT count(*) FROM logic_edges_v WHERE market_id_src != market_id_dst AND coalesce(event_slug_src,'') != coalesce(event_slug_dst,'')"
            )
            or 0
        )
        conditional_count = int(db.scalar("SELECT count(*) FROM conditional_edges_v") or 0)
        stats: dict[str, object] = {
            "candidate_edges": candidate_count,
            "logic_edges": edge_count,
            "deterministic_logic_edges": edge_count,
            "model_logic_edges": 0,
            "conditional_edges": conditional_count,
            "same_market_complement_edges": complement_count,
            "same_market_categorical_exclusion_edges": exclusion_count,
            "cross_market_deterministic_edges": cross_market_count,
            "cross_event_deterministic_edges": cross_event_count,
            "quarantined_pairs": 0,
            "rejected_edges": 0,
            "parse_failures": 0,
            "coverage": coverage,
            "candidate_workspace": {
                "database_bytes": database_path.stat().st_size,
                "spill_bytes": _tree_bytes(spill),
                "python_rows_materialized": proposition_count,
                "state_rows": {
                    "embeddings": 0,
                    "semantic_neighbors": 0,
                    "candidate_components": int(
                        db.scalar("SELECT count(*) FROM candidate_components_v") or 0
                    ),
                },
            },
            "solver": {
                "components": int(
                    db.scalar("SELECT count(DISTINCT solver_component_id) FROM logic_edges_v") or 0
                ),
                "accepted": edge_count,
                "rejected": 0,
                "hard_facts": edge_count,
            },
            "rules": {
                "version": RULE_VERSION,
                "support_required": {"positive": 100, "adversarial": 100},
                "enabled": sorted(emitted_rule_ids),
                "qualification": rule_support,
            },
            "active_markets": active_market_count,
            "closed_markets": closed_market_count,
            "fast_stage_timings": fast_timings,
        }
        write_reports(db, staging, stats)
        mark("reports")
        _drop_transient_tables(db)
        db.execute("CHECKPOINT")
        mark("cleanup_checkpoint")
        stats["publication_bytes"] = _tree_bytes(staging)
        return stats
    finally:
        db.close()
        shutil.rmtree(staging / ".duckdb-spill", ignore_errors=True)


def _insert_extracted_propositions(db: DuckDB, markets: list[SourceMarket]) -> int:
    batch: list[dict[str, Any]] = []
    count = 0
    for market in markets:
        for outcome in market.outcomes:
            extracted = extract_proposition(market, outcome)
            batch.append(_fast_proposition_row(market, outcome.outcome_index, outcome.outcome, outcome.clob_token_id, extracted))
            count += 1
            if len(batch) >= 16_384:
                insert_rows(
                    db,
                    "fast_propositions",
                    _FAST_INTERNAL_COLUMNS,
                    batch,
                    chunk_size=16_384,
                )
                batch.clear()
    if batch:
        insert_rows(
            db,
            "fast_propositions",
            _FAST_INTERNAL_COLUMNS,
            batch,
            chunk_size=16_384,
        )
    return count


def _fast_proposition_row(
    market: SourceMarket,
    outcome_index: int,
    outcome: str,
    token: str,
    extracted: ExtractedProposition,
) -> dict[str, Any]:
    interval = _numeric_interval(extracted)
    low, low_inc, high, high_inc = interval or (None, False, None, False)
    return {
        "proposition_id": token,
        "market_id": market.market_id,
        "event_id": market.event_id,
        "event_slug": market.event_slug,
        "clob_token_id": token,
        "outcome_index": outcome_index,
        "outcome": outcome,
        "question": market.question,
        "description": market.description,
        "market_source_hash": market.source_hash,
        "normalization_version": NORMALIZATION_VERSION,
        "category": market.category,
        "tags": list(market.tags),
        "subject_original": list(extracted.subject),
        "subject": list(extracted.subject),
        "predicate": extracted.predicate,
        "object_original": None,
        "object": None,
        "operator": extracted.operator,
        "threshold": extracted.threshold,
        "unit_original": extracted.unit,
        "unit": extracted.unit,
        "time_start": extracted.time_start,
        "time_end": extracted.time_end,
        "competition_original": extracted.competition,
        "competition": extracted.competition,
        "event_scope_original": extracted.event_scope,
        "event_scope": extracted.event_scope,
        "jurisdiction_original": extracted.jurisdiction,
        "jurisdiction": extracted.jurisdiction,
        "polarity": extracted.polarity,
        "parse_confidence": 1.0,
        "parse_status": "parsed",
        "primary_parser_model": None,
        "verifier_parser_model": None,
        "prompt_version": None,
        "primary_parse_fingerprint": None,
        "verifier_parse_fingerprint": None,
        "consensus_fingerprint": None,
        "automation_profile_id": None,
        "source_schema": SOURCE_SCHEMA,
        "extractor_id": EXTRACTOR_ID,
        "extractor_version": EXTRACTOR_VERSION,
        "extraction_status": extracted.status,
        "source_spans_json": extracted.spans_json(),
        "proof_scope_key": extracted.proof_scope_key,
        "expected_tokens": len(market.outcomes),
        "is_active": market.is_active,
        "is_closed": market.is_closed,
        "first_seen_ts": market.first_seen_ts or market.time_start,
        "last_seen_ts": market.last_seen_ts or market.time_end,
        "resolution_signature": extracted.resolution_signature,
        "numeric_predicate_signature": (
            extracted.numeric_predicate_signature if interval is not None else None
        ),
        "temporal_predicate_signature": extracted.temporal_predicate_signature,
        "stage_family_signature": extracted.stage_family_signature,
        "winner_family_signature": extracted.winner_family_signature,
        "stage_rank": _STAGE_RANK.get(extracted.stage or ""),
        "singular_winner": extracted.singular_winner,
        "rule_applicability_fingerprint": extracted.rule_applicability_fingerprint,
        "interval_low": low,
        "interval_low_inclusive": low_inc,
        "interval_high": high,
        "interval_high_inclusive": high_inc,
    }


def _numeric_interval(
    extracted: ExtractedProposition,
) -> tuple[float | None, bool, float | None, bool] | None:
    if extracted.interval_low is not None or extracted.interval_high is not None:
        if extracted.polarity == "negative":
            # The negation of a bounded interval is a union of two intervals;
            # do not collapse it into an unsafe one-interval proof.
            return None
        return (
            extracted.interval_low,
            extracted.interval_low_inclusive,
            extracted.interval_high,
            extracted.interval_high_inclusive,
        )
    if extracted.threshold is None or extracted.operator is None:
        return None
    operator = extracted.operator
    if extracted.polarity == "negative":
        negated_operators: dict[Operator, Operator] = {
            "greater_than": "less_than_or_equal",
            "greater_than_or_equal": "less_than",
            "less_than": "greater_than_or_equal",
            "less_than_or_equal": "greater_than",
        }
        negated_operator = negated_operators.get(operator)
        if negated_operator is None:
            # "not equal" is also a union of two intervals.
            return None
        operator = negated_operator
    threshold = extracted.threshold
    if operator == "greater_than":
        return threshold, False, None, False
    if operator == "greater_than_or_equal":
        return threshold, True, None, False
    if operator == "less_than":
        return None, False, threshold, False
    if operator == "less_than_or_equal":
        return None, False, threshold, True
    return threshold, True, threshold, True


def _create_public_base_tables(db: DuckDB) -> None:
    db.execute(
        "CREATE TABLE propositions_v AS SELECT "
        + ", ".join(PROPOSITION_COLUMNS)
        + " FROM fast_propositions"
    )
    db.execute(
        """
        CREATE TABLE nodes_table AS
        SELECT proposition_id AS node_id, market_id, outcome_index,
               clob_token_id, question, outcome AS outcome_label,
               coalesce(event_slug, event_id, '') AS event_slug,
               is_active, is_closed,
               CASE WHEN singular_winner THEN 'single_winner'
                    WHEN stage_rank IS NOT NULL THEN 'stage_progression'
                    ELSE 'unknown' END AS market_family,
               CASE WHEN expected_tokens = 2 AND outcome_index = 1
                    THEN 'NOT(' || question || ')'
                    WHEN expected_tokens = 2 THEN question
                    ELSE question || ' :: ' || outcome END AS canonical_proposition,
               CASE WHEN expected_tokens = 2 THEN 'binary' ELSE 'named_outcome' END
                    AS proposition_type,
               expected_tokens, first_seen_ts, last_seen_ts
        FROM fast_propositions
        ORDER BY proposition_id
        """
    )
    db.execute(
        """
        CREATE TEMP VIEW nodes_v AS
        SELECT n.*, p.subject[1] AS stage_subject,
               CASE WHEN p.stage_rank IS NOT NULL THEN p.predicate ELSE NULL END AS stage_key
        FROM nodes_table n
        JOIN fast_propositions p ON p.proposition_id=n.node_id
        """
    )
    db.execute(
        """
        CREATE TEMP TABLE market_groups_v AS
        SELECT market_id, coalesce(min(event_slug), min(event_id), '') AS event_slug,
               min(question) AS question,
               CASE WHEN bool_or(singular_winner) THEN 'single_winner'
                    WHEN count(stage_rank) > 0 THEN 'stage_progression'
                    ELSE 'unknown' END AS market_family,
               count(*)::INTEGER AS num_tokens,
               list(proposition_id ORDER BY outcome_index) AS token_ids,
               list(outcome ORDER BY outcome_index) AS outcome_labels,
               bool_or(is_active) AS is_active, bool_or(is_closed) AS is_closed,
               min(first_seen_ts) AS first_seen_ts, max(last_seen_ts) AS last_seen_ts
        FROM fast_propositions GROUP BY market_id ORDER BY market_id
        """
    )


def _create_fast_candidates(db: DuckDB, max_candidates: int) -> None:
    same_scope = _same_authoritative_scope_sql("a", "b")
    same_event = _same_event_sql("a", "b")
    db.execute(
        f"""
        CREATE TEMP TABLE fast_candidate_reason_rows AS
        WITH pairs AS (
            SELECT a.proposition_id a_id, b.proposition_id b_id, 'same_market' reason
            FROM fast_propositions a JOIN fast_propositions b
              ON a.market_id=b.market_id AND a.outcome_index<b.outcome_index
            UNION ALL
            SELECT a.proposition_id, b.proposition_id, 'resolution_signature'
            FROM fast_propositions a JOIN fast_propositions b
              ON a.resolution_signature=b.resolution_signature
             AND a.proposition_id<b.proposition_id AND a.market_id!=b.market_id
             AND ({same_scope})
            UNION ALL
            SELECT a.proposition_id, b.proposition_id, 'numeric_signature'
            FROM fast_propositions a JOIN fast_propositions b
              ON a.numeric_predicate_signature=b.numeric_predicate_signature
             AND a.numeric_predicate_signature IS NOT NULL
             AND a.proposition_id<b.proposition_id AND a.market_id!=b.market_id
             AND a.extraction_status='exact' AND b.extraction_status='exact'
             AND ({same_scope})
            UNION ALL
            SELECT a.proposition_id, b.proposition_id, 'temporal_signature'
            FROM fast_propositions a JOIN fast_propositions b
              ON a.temporal_predicate_signature=b.temporal_predicate_signature
             AND a.temporal_predicate_signature IS NOT NULL
             AND a.proposition_id<b.proposition_id AND a.market_id!=b.market_id
             AND a.extraction_status='exact' AND b.extraction_status='exact'
             AND ({same_scope})
            UNION ALL
            SELECT a.proposition_id, b.proposition_id, 'stage_family'
            FROM fast_propositions a JOIN fast_propositions b
              ON a.stage_family_signature=b.stage_family_signature
             AND a.stage_family_signature IS NOT NULL
             AND a.proposition_id<b.proposition_id AND a.market_id!=b.market_id
             AND a.extraction_status='exact' AND b.extraction_status='exact'
             AND ({same_scope})
            UNION ALL
            SELECT a.proposition_id, b.proposition_id, 'single_winner_family'
            FROM fast_propositions a JOIN fast_propositions b
              ON a.winner_family_signature=b.winner_family_signature
             AND a.winner_family_signature IS NOT NULL
             AND a.proposition_id<b.proposition_id AND a.market_id!=b.market_id
             AND a.extraction_status='exact' AND b.extraction_status='exact'
             AND ({same_event})
        ) SELECT DISTINCT a_id, b_id, reason FROM pairs
        """
    )
    pair_count = int(
        db.scalar("SELECT count(*) FROM (SELECT DISTINCT a_id,b_id FROM fast_candidate_reason_rows)") or 0
    )
    if pair_count > max_candidates:
        raise RuntimeError(
            f"Deterministic candidates ({pair_count}) exceed max_candidates={max_candidates}; refusing to truncate proofs"
        )
    subset_a_b = _interval_subset_sql("", "b_")
    subset_b_a = _interval_subset_sql("b_", "")
    db.execute(
        f"""
        CREATE TEMP TABLE fast_relations AS
        WITH grouped AS (
            SELECT a_id, b_id, list(DISTINCT reason ORDER BY reason) reasons
            FROM fast_candidate_reason_rows GROUP BY a_id,b_id
        ), joined AS (
            SELECT g.*,
                   a.market_id, a.event_slug, a.event_id,
                   a.event_scope,
                   a.source_spans_json,
                   a.rule_applicability_fingerprint,
                   a.proof_scope_key,
                   a.resolution_signature,
                   a.numeric_predicate_signature,
                   a.temporal_predicate_signature,
                   a.stage_family_signature,
                   a.winner_family_signature,
                   a.stage_rank, a.subject, a.polarity, a.time_start, a.time_end,
                   a.interval_low, a.interval_low_inclusive,
                   a.interval_high, a.interval_high_inclusive,
                   a.expected_tokens,
                   b.market_id b_market_id, b.event_slug b_event_slug,
                   b.event_id b_event_id, b.event_scope b_event_scope,
                   b.source_spans_json b_source_spans_json,
                   ({same_scope}) AS same_authoritative_scope,
                   ({same_event}) AS same_event,
                   b.rule_applicability_fingerprint b_rule_fingerprint,
                   b.proof_scope_key b_proof_scope_key,
                   b.resolution_signature b_resolution_signature,
                   b.numeric_predicate_signature b_numeric_signature,
                   b.temporal_predicate_signature b_temporal_signature,
                   b.stage_family_signature b_stage_family,
                   b.winner_family_signature b_winner_family,
                   b.stage_rank b_stage_rank, b.subject b_subject,
                   b.polarity b_polarity,
                   b.time_start b_time_start, b.time_end b_time_end,
                   b.interval_low b_interval_low,
                   b.interval_low_inclusive b_interval_low_inclusive,
                   b.interval_high b_interval_high,
                   b.interval_high_inclusive b_interval_high_inclusive,
                   b.expected_tokens b_expected_tokens
            FROM grouped g
            JOIN fast_propositions a ON a.proposition_id=g.a_id
            JOIN fast_propositions b ON b.proposition_id=g.b_id
        ), classified AS (
            SELECT *,
              CASE
                WHEN market_id=b_market_id AND expected_tokens=2 THEN 'complement'
                WHEN market_id=b_market_id THEN 'mutually_exclusive'
                WHEN resolution_signature=b_resolution_signature
                     AND same_authoritative_scope THEN 'equivalent'
                WHEN numeric_predicate_signature=b_numeric_signature
                     AND same_authoritative_scope
                     AND interval_low IS NOT NULL AND interval_high IS NOT NULL
                     AND b_interval_low IS NOT NULL AND b_interval_high IS NOT NULL
                     AND (
                         interval_high < b_interval_low OR b_interval_high < interval_low OR
                         (interval_high = b_interval_low AND NOT (interval_high_inclusive AND b_interval_low_inclusive)) OR
                         (b_interval_high = interval_low AND NOT (b_interval_high_inclusive AND interval_low_inclusive))
                     ) THEN 'mutually_exclusive'
                WHEN numeric_predicate_signature=b_numeric_signature
                     AND same_authoritative_scope
                     AND ({subset_a_b}) AND ({subset_b_a}) THEN 'equivalent'
                WHEN numeric_predicate_signature=b_numeric_signature
                     AND same_authoritative_scope AND ({subset_a_b}) THEN 'A_implies_B'
                WHEN numeric_predicate_signature=b_numeric_signature
                     AND same_authoritative_scope AND ({subset_b_a}) THEN 'B_implies_A'
                WHEN temporal_predicate_signature=b_temporal_signature
                     AND same_authoritative_scope
                     AND polarity='positive' AND b_polarity='positive'
                     AND (b_time_start IS NULL OR (time_start IS NOT NULL AND time_start>=b_time_start))
                     AND (b_time_end IS NULL OR (time_end IS NOT NULL AND time_end<=b_time_end))
                     AND (time_start IS DISTINCT FROM b_time_start OR time_end IS DISTINCT FROM b_time_end)
                     THEN 'A_implies_B'
                WHEN temporal_predicate_signature=b_temporal_signature
                     AND same_authoritative_scope
                     AND polarity='positive' AND b_polarity='positive'
                     AND (time_start IS NULL OR (b_time_start IS NOT NULL AND b_time_start>=time_start))
                     AND (time_end IS NULL OR (b_time_end IS NOT NULL AND b_time_end<=time_end))
                     AND (time_start IS DISTINCT FROM b_time_start OR time_end IS DISTINCT FROM b_time_end)
                     THEN 'B_implies_A'
                WHEN temporal_predicate_signature=b_temporal_signature
                     AND same_authoritative_scope
                     AND polarity='negative' AND b_polarity='negative'
                     AND time_start IS NULL AND b_time_start IS NULL
                     AND time_end>b_time_end THEN 'A_implies_B'
                WHEN temporal_predicate_signature=b_temporal_signature
                     AND same_authoritative_scope
                     AND polarity='negative' AND b_polarity='negative'
                     AND b_time_start IS NULL AND time_start IS NULL
                     AND b_time_end>time_end THEN 'B_implies_A'
                WHEN stage_family_signature=b_stage_family
                     AND same_authoritative_scope AND polarity='positive'
                     AND b_polarity='positive' AND stage_rank>b_stage_rank THEN 'A_implies_B'
                WHEN stage_family_signature=b_stage_family
                     AND same_authoritative_scope AND polarity='positive'
                     AND b_polarity='positive' AND b_stage_rank>stage_rank THEN 'B_implies_A'
                WHEN stage_family_signature=b_stage_family
                     AND same_authoritative_scope AND polarity='negative'
                     AND b_polarity='negative' AND stage_rank<b_stage_rank THEN 'A_implies_B'
                WHEN stage_family_signature=b_stage_family
                     AND same_authoritative_scope AND polarity='negative'
                     AND b_polarity='negative' AND b_stage_rank<stage_rank THEN 'B_implies_A'
                WHEN winner_family_signature=b_winner_family AND same_event
                     AND polarity='positive'
                     AND b_polarity='positive' AND subject!=b_subject THEN 'mutually_exclusive'
                ELSE NULL END AS relation,
              CASE
                WHEN market_id=b_market_id AND expected_tokens=2 THEN 'same_market.binary_complement.v1'
                WHEN market_id=b_market_id THEN 'same_market.categorical_exclusion.v1'
                WHEN resolution_signature=b_resolution_signature THEN 'equivalence.normalized_fields.v1'
                WHEN numeric_predicate_signature=b_numeric_signature THEN 'threshold.interval_containment.v2'
                WHEN temporal_predicate_signature=b_temporal_signature THEN 'time.interval_containment.v1'
                WHEN stage_family_signature=b_stage_family THEN 'tournament.stage_progression.v1'
                WHEN winner_family_signature=b_winner_family THEN 'event.single_winner.v1'
                ELSE NULL END AS selected_rule_id
            FROM joined
        ) SELECT * FROM classified WHERE relation IS NOT NULL
        """
    )
    candidate_projection = _candidate_projection_sql()
    db.execute(f"CREATE TABLE relation_candidates_work AS SELECT {candidate_projection} FROM fast_relations")
    db.execute("CREATE VIEW relation_candidates_v AS SELECT * FROM relation_candidates_work")


def _same_event_sql(left: str, right: str) -> str:
    return f"""
      (({left}.event_id IS NOT NULL AND {right}.event_id IS NOT NULL
        AND {left}.event_id={right}.event_id)
       OR
       ({left}.event_slug IS NOT NULL AND {right}.event_slug IS NOT NULL
        AND {left}.event_slug={right}.event_slug))
    """


def _same_authoritative_scope_sql(left: str, right: str) -> str:
    same_event = _same_event_sql(left, right)
    return f"""
      (({same_event})
       OR
       (nullif(trim({left}.event_scope), '') IS NOT NULL
        AND lower(regexp_replace(trim({left}.event_scope), '\\s+', ' ', 'g')) =
            lower(regexp_replace(trim({right}.event_scope), '\\s+', ' ', 'g'))))
    """


def _interval_subset_sql(inner: str, outer: str) -> str:
    return f"""
      ({outer}interval_low IS NULL OR
        ({inner}interval_low IS NOT NULL AND
          ({inner}interval_low>{outer}interval_low OR
           ({inner}interval_low={outer}interval_low AND
             ({outer}interval_low_inclusive OR NOT {inner}interval_low_inclusive)))))
      AND
      ({outer}interval_high IS NULL OR
        ({inner}interval_high IS NOT NULL AND
          ({inner}interval_high<{outer}interval_high OR
           ({inner}interval_high={outer}interval_high AND
             ({outer}interval_high_inclusive OR NOT {inner}interval_high_inclusive)))))
    """


def _candidate_projection_sql() -> str:
    values: dict[str, str] = {
        "proposition_a_id": "a_id",
        "proposition_b_id": "b_id",
        "candidate_reasons": "reasons",
        "deterministic_relation": "relation",
        "rule_id": "selected_rule_id",
        "rule_status": "'enabled'",
        "a_implies_b": "relation IN ('A_implies_B','equivalent')",
        "b_implies_a": "relation IN ('B_implies_A','equivalent')",
        "explanation": "'Exact deterministic proof from authoritative catalog fields'",
        "status": "'accepted'",
        "discovery_method": "'deterministic'",
        "evidence_tier": "CASE WHEN market_id=b_market_id THEN 'source_contract' ELSE 'deterministic_rule' END",
        "extractor_id": f"'{EXTRACTOR_ID}'",
        "extractor_version": f"'{EXTRACTOR_VERSION}'",
        "source_spans_json": "json_object('A',json(source_spans_json),'B',json(b_source_spans_json))::VARCHAR",
        "rule_applicability_fingerprint": "sha256(rule_applicability_fingerprint || '|' || b_rule_fingerprint || '|' || selected_rule_id)",
        "proof_scope_key": "CASE WHEN selected_rule_id LIKE 'same_market.%' THEN sha256('market|'||market_id) WHEN selected_rule_id LIKE 'equivalence.%' THEN resolution_signature WHEN selected_rule_id LIKE 'threshold.%' THEN numeric_predicate_signature WHEN selected_rule_id LIKE 'time.%' THEN temporal_predicate_signature WHEN selected_rule_id LIKE 'tournament.%' THEN stage_family_signature ELSE winner_family_signature END",
    }
    return ", ".join(
        f"{values.get(name, _candidate_null(name))} AS {name}"
        for name in CANDIDATE_COLUMNS
    )


def _candidate_null(name: str) -> str:
    sql_type = CANDIDATE_COLUMNS[name]
    if sql_type == "VARCHAR[]":
        return "[]::VARCHAR[]"
    if sql_type == "BOOLEAN":
        return "false::BOOLEAN"
    return f"NULL::{sql_type}"


def _create_fast_logic_edges(db: DuckDB) -> None:
    proof_scope = "CASE WHEN selected_rule_id LIKE 'same_market.%' THEN sha256('market|'||market_id) WHEN selected_rule_id LIKE 'equivalence.%' THEN resolution_signature WHEN selected_rule_id LIKE 'threshold.%' THEN numeric_predicate_signature WHEN selected_rule_id LIKE 'time.%' THEN temporal_predicate_signature WHEN selected_rule_id LIKE 'tournament.%' THEN stage_family_signature ELSE winner_family_signature END"
    columns: dict[str, str] = {
        "src_node_id": "CASE WHEN relation='B_implies_A' THEN b_id ELSE a_id END",
        "dst_node_id": "CASE WHEN relation='B_implies_A' THEN a_id ELSE b_id END",
        "edge_type": "CASE WHEN relation IN ('A_implies_B','B_implies_A') THEN 'implies' ELSE relation END",
        "edge_basis": "CASE WHEN selected_rule_id LIKE 'same_market.%' THEN 'same_market' WHEN selected_rule_id LIKE 'equivalence.%' THEN 'normalized_equivalence' WHEN selected_rule_id LIKE 'threshold.%' THEN 'numeric_threshold' WHEN selected_rule_id LIKE 'time.%' THEN 'time_window_containment' WHEN selected_rule_id LIKE 'tournament.%' THEN 'tournament_stage' ELSE 'single_winner' END",
        "confidence": "1.0::DOUBLE",
        "market_id_src": "CASE WHEN relation='B_implies_A' THEN b_market_id ELSE market_id END",
        "market_id_dst": "CASE WHEN relation='B_implies_A' THEN market_id ELSE b_market_id END",
        "event_slug_src": "CASE WHEN relation='B_implies_A' THEN coalesce(b_event_slug,b_event_id,'') ELSE coalesce(event_slug,event_id,'') END",
        "event_slug_dst": "CASE WHEN relation='B_implies_A' THEN coalesce(event_slug,event_id,'') ELSE coalesce(b_event_slug,b_event_id,'') END",
        "evidence": "'Exact deterministic proof from authoritative source fields'",
        "discovery_method": "'deterministic'",
        "rule_version": f"'{RULE_VERSION}'",
        "prompt_version": "NULL::VARCHAR",
        "explanation": "'Exact deterministic proof from authoritative catalog fields'",
        "assumptions": "[]::VARCHAR[]",
        "rule_id": "selected_rule_id",
        "proposal_id": "sha256(a_id||'|'||b_id||'|'||relation||'|'||selected_rule_id)",
        "solver_version": f"'{SOLVER_VERSION}'",
        "constraint_version": f"'{CONSTRAINT_VERSION}'",
        "solver_component_id": f"sha256('solver|'||({proof_scope}))",
        "evidence_tier": "CASE WHEN market_id=b_market_id THEN 'source_contract' ELSE 'deterministic_rule' END",
        "extractor_id": f"'{EXTRACTOR_ID}'",
        "extractor_version": f"'{EXTRACTOR_VERSION}'",
        "source_spans_json": "json_object('A',json(source_spans_json),'B',json(b_source_spans_json))::VARCHAR",
        "rule_applicability_fingerprint": "sha256(rule_applicability_fingerprint||'|'||b_rule_fingerprint||'|'||selected_rule_id)",
        "proof_scope_key": proof_scope,
    }
    projection = ", ".join(
        f"{columns.get(name, 'NULL::VARCHAR')} AS {name}" for name in LOGIC_EDGE_COLUMNS
    )
    db.execute(f"CREATE TABLE logic_edges_v AS SELECT {projection} FROM fast_relations ORDER BY 1,2,3")


def _validate_deterministic_invariants(db: DuckDB) -> None:
    invalid = int(
        db.scalar(
            """
            SELECT count(*) FROM logic_edges_v
            WHERE src_node_id=dst_node_id OR rule_id IS NULL
               OR evidence_tier NOT IN ('source_contract','deterministic_rule')
               OR source_spans_json IS NULL OR proof_scope_key IS NULL
            """
        )
        or 0
    )
    duplicates = int(
        db.scalar(
            "SELECT count(*) FROM (SELECT src_node_id,dst_node_id,edge_type,count(*) n FROM logic_edges_v GROUP BY ALL HAVING n>1)"
        )
        or 0
    )
    conflicts = int(
        db.scalar(
            "SELECT count(*) FROM (SELECT least(src_node_id,dst_node_id) a,greatest(src_node_id,dst_node_id) b,count(DISTINCT edge_type) n FROM logic_edges_v GROUP BY 1,2 HAVING n>1)"
        )
        or 0
    )
    invalid_proofs = int(
        db.scalar(
            """
            SELECT count(*)
            FROM fast_relations
            WHERE NOT coalesce(CASE
                WHEN selected_rule_id='same_market.binary_complement.v1'
                    THEN market_id=b_market_id AND expected_tokens=2
                WHEN selected_rule_id='same_market.categorical_exclusion.v1'
                    THEN market_id=b_market_id AND expected_tokens>2
                WHEN selected_rule_id='equivalence.normalized_fields.v1'
                    THEN resolution_signature=b_resolution_signature
                WHEN selected_rule_id='threshold.interval_containment.v2'
                    THEN numeric_predicate_signature=b_numeric_signature
                         AND numeric_predicate_signature IS NOT NULL
                WHEN selected_rule_id='time.interval_containment.v1'
                    THEN temporal_predicate_signature=b_temporal_signature
                         AND temporal_predicate_signature IS NOT NULL
                         AND (time_start IS DISTINCT FROM b_time_start
                              OR time_end IS DISTINCT FROM b_time_end)
                WHEN selected_rule_id='tournament.stage_progression.v1'
                    THEN stage_family_signature=b_stage_family
                         AND stage_family_signature IS NOT NULL
                         AND stage_rank!=b_stage_rank
                WHEN selected_rule_id='event.single_winner.v1'
                    THEN winner_family_signature=b_winner_family
                         AND winner_family_signature IS NOT NULL
                         AND polarity='positive' AND b_polarity='positive'
                         AND subject!=b_subject
                ELSE false
            END, false)
            """
        )
        or 0
    )
    if invalid or duplicates or conflicts or invalid_proofs:
        raise RuntimeError(
            "Deterministic proof validation failed: "
            f"invalid={invalid}, duplicates={duplicates}, conflicts={conflicts}, "
            f"invalid_proofs={invalid_proofs}"
        )


def _create_empty_diagnostics(db: DuckDB) -> None:
    create_and_fill(db, "rejected_edges_v", REJECTED_EDGE_COLUMNS, [])
    create_and_fill(db, "parse_errors_v", PARSE_ERROR_COLUMNS, [], temporary=True)
    create_and_fill(db, "model_assessments_v", MODEL_ASSESSMENT_COLUMNS, [], temporary=True)
    create_and_fill(db, "quarantined_pairs_v", QUARANTINE_COLUMNS, [])
    db.execute(
        """
        CREATE TEMP TABLE parse_assessments_v AS
        SELECT sha256(proposition_id||'|deterministic') assessment_id,
               proposition_id, market_id,
               'deterministic_extractor'::VARCHAR assessor_type,
               NULL::VARCHAR model_role, NULL::VARCHAR model_version,
               sha256(extractor_id||'|'||extractor_version||'|'||market_source_hash)
                   inference_fingerprint,
               extraction_status status, parse_confidence confidence,
               json_object('subject',subject,'predicate',predicate,'operator',operator,
                           'threshold',threshold,'unit',unit,'time_start',time_start,
                           'time_end',time_end,'proof_scope_key',proof_scope_key)::VARCHAR parsed_json,
               ['question','outcome']::VARCHAR[] citations,
               NULL::VARCHAR validation_error, []::VARCHAR[] authoritative_conflicts
        FROM propositions_v ORDER BY proposition_id
        """
    )
    create_and_fill(
        db,
        "qualification_cases_v",
        QUALIFICATION_CASE_COLUMNS,
        [],
        temporary=True,
    )


def _create_incremental_state(db: DuckDB) -> None:
    db.execute(
        f"""
        CREATE TEMP TABLE market_state_v AS
        SELECT market_id, min(market_source_hash) source_hash,
               '{EXTRACTOR_ID}'::VARCHAR parse_model,
               '{EXTRACTOR_VERSION}'::VARCHAR parse_prompt_version,
               '{NORMALIZATION_VERSION}'::VARCHAR normalization_version,
               '{RULE_VERSION}'::VARCHAR rule_version
        FROM propositions_v GROUP BY market_id
        """
    )
    db.execute(
        f"""
        CREATE TEMP TABLE proposition_fingerprints_v AS
        SELECT proposition_id, market_id, market_source_hash,
               sha256(coalesce(source_spans_json,'')||'|'||coalesce(proof_scope_key,'')) parse_fingerprint,
               '{NORMALIZATION_VERSION}'::VARCHAR normalization_version
        FROM propositions_v
        """
    )
    create_and_fill(db, "proposition_embeddings_v", EMBEDDING_STATE_COLUMNS, [], temporary=True)
    create_and_fill(db, "semantic_neighbors_v", SEMANTIC_NEIGHBOR_STATE_COLUMNS, [], temporary=True)
    db.execute(
        f"""
        CREATE TEMP TABLE candidate_components_v AS
        SELECT sha256(coalesce(proof_scope_key, proposition_a_id||'|'||proposition_b_id)) component_id,
               sha256(string_agg(proposition_a_id||'|'||proposition_b_id||'|'||coalesce(rule_id,''), '|' ORDER BY proposition_a_id,proposition_b_id)) component_fingerprint,
               count(*)::INTEGER pair_count, '{CANDIDATE_STATE_VERSION}'::VARCHAR candidate_version
        FROM relation_candidates_v
        GROUP BY coalesce(proof_scope_key, proposition_a_id||'|'||proposition_b_id)
        """
    )
    db.execute(
        f"""
        CREATE TEMP TABLE candidate_blocks_v AS
        SELECT sha256(reason) block_id, reason reason_kind, reason group_key,
               sha256(string_agg(a_id||'|'||b_id, '|' ORDER BY a_id,b_id)) member_fingerprint,
               count(DISTINCT (a_id,b_id))::INTEGER member_count,
               '{CANDIDATE_STATE_VERSION}'::VARCHAR candidate_version
        FROM fast_candidate_reason_rows GROUP BY reason
        """
    )
    db.execute(
        f"""
        CREATE TEMP TABLE candidate_reason_rows_v AS
        SELECT sha256(reason) block_id, a_id proposition_a_id, b_id proposition_b_id,
               reason, NULL::DOUBLE embedding_similarity, NULL::INTEGER embedding_rank,
               '{CANDIDATE_STATE_VERSION}'::VARCHAR candidate_version
        FROM fast_candidate_reason_rows
        """
    )
    db.execute(
        f"""
        CREATE TEMP TABLE solver_components_v AS
        SELECT solver_component_id,
               sha256(string_agg(proposal_id, '|' ORDER BY proposal_id)) proposal_hash,
               list(proposal_id ORDER BY proposal_id)::VARCHAR[] accepted_proposal_ids,
               []::VARCHAR[] rejected_proposal_ids,
               count(*)::INTEGER proposal_count, count(*)::INTEGER hard_clause_count,
               0::INTEGER soft_clause_count, 0::BIGINT objective_cost,
               '{SOLVER_VERSION}'::VARCHAR solver_version,
               '{CONSTRAINT_VERSION}'::VARCHAR constraint_version
        FROM logic_edges_v
        GROUP BY solver_component_id
        """
    )
    db.execute(
        f"""
        CREATE TEMP TABLE execution_plan_v AS
        SELECT 'markets'::VARCHAR stage, 'market'::VARCHAR unit_type,
               market_id unit_id, 'recomputed'::VARCHAR status,
               []::VARCHAR[] dependency_ids,
               ['clean_build']::VARCHAR[] invalidation_reasons,
               NULL::VARCHAR input_fingerprint, source_hash output_fingerprint,
               '{EXECUTION_PLAN_VERSION}'::VARCHAR plan_version
        FROM market_state_v
        """
    )


def _export_artifacts(db: DuckDB, staging: Path) -> None:
    exports: tuple[tuple[str, str, dict[str, str], str], ...] = (
        ("nodes_table", "nodes.parquet", NODE_COLUMNS, "node_id"),
        ("market_groups_v", "market_groups.parquet", MARKET_GROUP_COLUMNS, "market_id"),
        ("propositions_v", "propositions.parquet", PROPOSITION_COLUMNS, "proposition_id"),
        ("relation_candidates_v", "relation_candidates.parquet", CANDIDATE_COLUMNS, "proposition_a_id,proposition_b_id"),
        ("logic_edges_v", "logic_edges.parquet", LOGIC_EDGE_COLUMNS, "src_node_id,dst_node_id,edge_type"),
        ("parse_assessments_v", "parse_assessments.parquet", PARSE_ASSESSMENT_COLUMNS, "assessment_id"),
        ("model_assessments_v", "model_assessments.parquet", MODEL_ASSESSMENT_COLUMNS, "assessment_id"),
        ("quarantined_pairs_v", "quarantined_pairs.parquet", QUARANTINE_COLUMNS, "quarantine_id"),
        ("rejected_edges_v", "rejected_edges.parquet", REJECTED_EDGE_COLUMNS, "proposal_id"),
        ("parse_errors_v", "parse_errors.parquet", PARSE_ERROR_COLUMNS, "error_id"),
        ("event_summary_v", "event_summary.parquet", EVENT_SUMMARY_COLUMNS, "event_key"),
        ("event_relation_summary_v", "event_relation_summary.parquet", EVENT_RELATION_SUMMARY_COLUMNS, "src_event_key,dst_event_key,edge_type"),
        ("component_summary_v", "component_summary.parquet", COMPONENT_SUMMARY_COLUMNS, "component_id"),
        ("node_metrics_v", "node_metrics.parquet", NODE_METRIC_COLUMNS, "node_id"),
        ("visualization_layout_v", "visualization_layout.parquet", VISUALIZATION_LAYOUT_COLUMNS, "layout_level,object_id"),
    )
    for table, name, columns, order in exports:
        copy_sorted_parquet(db, table, staging / name, list(columns), order)
    copy_sorted_parquet(db, "qualification_cases_v", staging / "qualification_cases.parquet", list(QUALIFICATION_CASE_COLUMNS), "case_id")
    state_dir = staging / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_exports = (
        ("market_state_v", "market_state.parquet", MARKET_STATE_COLUMNS, "market_id"),
        ("proposition_fingerprints_v", "proposition_fingerprints.parquet", PROPOSITION_FINGERPRINT_COLUMNS, "proposition_id"),
        ("proposition_embeddings_v", "proposition_embeddings.parquet", EMBEDDING_STATE_COLUMNS, "proposition_id"),
        ("semantic_neighbors_v", "semantic_neighbors.parquet", SEMANTIC_NEIGHBOR_STATE_COLUMNS, "proposition_id,neighbor_rank"),
        ("candidate_components_v", "candidate_components.parquet", CANDIDATE_COMPONENT_STATE_COLUMNS, "component_id"),
        ("candidate_blocks_v", "candidate_blocks.parquet", CANDIDATE_BLOCK_COLUMNS, "block_id"),
        ("candidate_reason_rows_v", "candidate_reason_rows.parquet", CANDIDATE_REASON_COLUMNS, "block_id,proposition_a_id,proposition_b_id"),
        ("solver_components_v", "solver_components.parquet", SOLVER_COMPONENT_STATE_COLUMNS, "solver_component_id"),
        ("execution_plan_v", "execution_plan.parquet", EXECUTION_PLAN_COLUMNS, "stage,unit_type,unit_id"),
    )
    for table, name, columns, order in state_exports:
        copy_sorted_parquet(db, table, state_dir / name, list(columns), order)


def _write_viewer_manifest(db: DuckDB, staging: Path, coverage: dict[str, object]) -> None:
    content_names = (
        "nodes.parquet", "propositions.parquet", "relation_candidates.parquet",
        "logic_edges.parquet", "event_summary.parquet", "component_summary.parquet",
        "node_metrics.parquet", "visualization_layout.parquet",
    )
    atomic_write_json(
        staging / "viewer_manifest.json",
        {
            "schema_version": VIEWER_ARTIFACT_VERSION,
            "api_version": VIEWER_API_VERSION,
            "layout_version": VISUALIZATION_LAYOUT_VERSION,
            "build_mode": "fast",
            "validation_status": "DETERMINISTIC_VALIDATED",
            "evidence_tiers": ["source_contract", "deterministic_rule"],
            "source_watermark": db.scalar("SELECT max(last_seen_ts) FROM nodes_table"),
            "graph_content_fingerprint": canonical_json_sha256(
                {"coverage": coverage, "artifacts": {name: sha256_file(staging / name) for name in content_names}}
            ),
            "response_limits": {"nodes": 5_000, "edges": 10_000},
        },
    )


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _drop_transient_tables(db: DuckDB) -> None:
    db.execute("DROP VIEW IF EXISTS nodes_v")
    for table in (
        "fast_propositions",
        "fast_candidate_reason_rows",
        "fast_relations",
        "market_groups_v",
        "parse_assessments_v",
        "model_assessments_v",
        "parse_errors_v",
        "qualification_cases_v",
        "market_state_v",
        "proposition_fingerprints_v",
        "proposition_embeddings_v",
        "semantic_neighbors_v",
        "candidate_components_v",
        "candidate_blocks_v",
        "candidate_reason_rows_v",
        "solver_components_v",
        "execution_plan_v",
        "market_domains_v",
    ):
        db.execute(f"DROP TABLE IF EXISTS {table}")
    db.execute(
        """
        CREATE VIEW nodes_v AS
        SELECT n.*, NULL::VARCHAR AS stage_subject, NULL::VARCHAR AS stage_key
        FROM nodes_table n
        """
    )
