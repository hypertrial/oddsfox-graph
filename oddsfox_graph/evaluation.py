from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from ._discovery.bulk import create_and_fill
from ._discovery.evaluation_metrics import (
    calibration as _calibration,
    f1 as _f1,
    precision as _precision,
)
from ._discovery.input import load_source_markets
from .queries import DuckDB, q
from ._discovery.incremental import ExecutionPlan
from ._discovery.versions import (
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_VERSION,
    DOMAIN_TAXONOMY_VERSION,
)
CANONICAL_SOURCE_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)
DOMAINS = (
    "sports",
    "elections",
    "cryptocurrency",
    "economic_indicators",
    "date_based",
)
RELATIONS = {
    "equivalent",
    "A_implies_B",
    "B_implies_A",
    "mutually_exclusive",
    "complement",
    "compatible",
    "unrelated",
    "uncertain",
}
POSITIVE_RELATIONS = RELATIONS - {"unrelated", "uncertain"}

REVIEW_FIELDS = (
    "record_id",
    "record_type",
    "reviewer_alias",
    "domain",
    "proposition_a_id",
    "proposition_b_id",
    "question_a",
    "description_a",
    "outcome_a",
    "question_b",
    "description_b",
    "outcome_b",
    "expected_subjects_json",
    "expected_predicate",
    "expected_object",
    "expected_operator",
    "expected_threshold",
    "expected_unit",
    "expected_time_start",
    "expected_time_end",
    "expected_competition",
    "expected_event_scope",
    "expected_jurisdiction",
    "expected_polarity",
    "expected_relation",
    "unsupported_assumption",
    "reviewer_notes",
)

BENCHMARK_COLUMNS = {
    "benchmark_version": "VARCHAR",
    "schema_version": "VARCHAR",
    "source_sha256": "VARCHAR",
    "sampling_manifest_sha256": "VARCHAR",
    "partition": "VARCHAR",
    "pair_source": "VARCHAR",
    "record_id": "VARCHAR",
    "record_type": "VARCHAR",
    "domain": "VARCHAR",
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "expected_subjects": "VARCHAR[]",
    "expected_predicate": "VARCHAR",
    "expected_object": "VARCHAR",
    "expected_operator": "VARCHAR",
    "expected_threshold": "DOUBLE",
    "expected_unit": "VARCHAR",
    "expected_time_start": "TIMESTAMPTZ",
    "expected_time_end": "TIMESTAMPTZ",
    "expected_competition": "VARCHAR",
    "expected_event_scope": "VARCHAR",
    "expected_jurisdiction": "VARCHAR",
    "expected_polarity": "VARCHAR",
    "expected_relation": "VARCHAR",
    "unsupported_assumption": "BOOLEAN",
    "reviewer_a_alias": "VARCHAR",
    "reviewer_a_label": "VARCHAR",
    "reviewer_a_notes": "VARCHAR",
    "reviewer_b_alias": "VARCHAR",
    "reviewer_b_label": "VARCHAR",
    "reviewer_b_notes": "VARCHAR",
    "disagreement": "BOOLEAN",
    "disagreement_fields": "VARCHAR[]",
    "final_notes": "VARCHAR",
}

_DOMAIN_PATTERNS = {
    "elections": re.compile(
        r"\b(election|elected|president|presidential|primary|nominee|"
        r"vote share|electoral|prime minister)\b",
        re.I,
    ),
    "cryptocurrency": re.compile(
        r"\b(bitcoin|btc|ethereum|eth|crypto|solana|xrp|dogecoin|blockchain)\b",
        re.I,
    ),
    "economic_indicators": re.compile(
        r"\b(gdp|inflation|cpi|unemployment|interest rates?|federal reserve|"
        r"recession|jobs report|nonfarm|payrolls?)\b",
        re.I,
    ),
    "sports": re.compile(
        r"\b(nba|nfl|nhl|mlb|fifa|uefa|champions league|world cup|"
        r"super bowl|premier league|tournament|championship|playoffs?)\b",
        re.I,
    ),
    "date_based": re.compile(
        r"\b(before|after|between|by|during|until|on)\b|"
        r"\b20\d{2}\b|\b(january|february|march|april|may|june|july|"
        r"august|september|october|november|december)\b",
        re.I,
    ),
}


def assign_domain(
    question: str,
    description: str = "",
    event_slug: str = "",
    category: str = "",
    event_id: str = "",
    tags: list[str] | tuple[str, ...] = (),
) -> str:
    text = " ".join(
        (
            question,
            description,
            event_slug,
            event_id,
            category,
            " ".join(tags),
        )
    )
    for domain in (
        "elections",
        "cryptocurrency",
        "economic_indicators",
        "sports",
        "date_based",
    ):
        if _DOMAIN_PATTERNS[domain].search(text):
            return domain
    return "other"


def export_benchmark_reviews(
    input_path: Path,
    out_dir: Path,
    output_dir: Path,
    *,
    parse_count: int = 750,
    pair_count: int = 3_000,
    seed: int = 0,
) -> dict[str, int]:
    if parse_count < len(DOMAINS) or pair_count < 1:
        raise ValueError("Benchmark sample sizes are too small")
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    output_dir = output_dir.resolve()
    propositions_path = out_dir / "propositions.parquet"
    candidates_path = out_dir / "relation_candidates.parquet"
    if not propositions_path.is_file() or not candidates_path.is_file():
        raise ValueError("Benchmark export requires completed discovery artifacts")
    manifest_path = out_dir / "build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Benchmark export requires a manifest-complete discovery build")
    source_hash = _sha256(input_path)
    if source_hash != CANONICAL_SOURCE_SHA256:
        raise ValueError(
            "Benchmark export requires the canonical supplied catalog"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("command") != "discover"
        or manifest.get("input_hash") != source_hash
    ):
        raise ValueError("Discovery output does not match the benchmark source parquet")
    _, _, top_markets, _ = load_source_markets(
        input_path,
        max_propositions=5_000,
    )
    top_proposition_ids = {
        outcome.clob_token_id
        for market in top_markets
        for outcome in market.outcomes
    }

    db = DuckDB()
    try:
        propositions = db.rows(
            f"""
            SELECT proposition_id, market_id, question, description, outcome,
                   event_id, event_slug, category, tags
            FROM read_parquet('{q(propositions_path)}')
            ORDER BY proposition_id
            """
        )
        candidates = db.rows(
            f"""
            SELECT proposition_a_id, proposition_b_id, candidate_reasons,
                   deterministic_relation, embedding_similarity
            FROM read_parquet('{q(candidates_path)}')
            ORDER BY proposition_a_id, proposition_b_id
            """
        )
    finally:
        db.close()

    propositions = [
        row
        for row in propositions
        if str(row["proposition_id"]) in top_proposition_ids
    ]
    for row in propositions:
        row["domain"] = assign_domain(
            str(row["question"]),
            str(row.get("description") or ""),
            str(row.get("event_slug") or ""),
            str(row.get("category") or ""),
            str(row.get("event_id") or ""),
            list(row.get("tags") or []),
        )
    selected = _stratified_propositions(propositions, parse_count, seed)
    by_id = {str(row["proposition_id"]): row for row in selected}
    selected_ids = set(by_id)
    selected_candidates = [
        row
        for row in candidates
        if str(row["proposition_a_id"]) in selected_ids
        and str(row["proposition_b_id"]) in selected_ids
    ]
    pairs = _benchmark_pairs(
        by_id,
        selected_candidates,
        pair_count,
        seed=seed + 1,
    )
    rows = [
        _parse_review_row(row)
        for row in sorted(selected, key=lambda item: str(item["proposition_id"]))
    ]
    rows.extend(_pair_review_row(row, by_id) for row in pairs)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, alias in (
        ("reviewer-a.csv", "reviewer-a"),
        ("reviewer-b.csv", "reviewer-b"),
        ("adjudication.csv", "adjudicator"),
    ):
        destination = output_dir / filename
        with destination.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "reviewer_alias": alias})
    sampling = {
        "benchmark_version": BENCHMARK_VERSION,
        "domain_taxonomy_version": DOMAIN_TAXONOMY_VERSION,
        "source_sha256": source_hash,
        "seed": seed,
        "parse_records": len(selected),
        "pair_records": len(pairs),
        "domains": dict(Counter(str(row["domain"]) for row in selected)),
        "pair_sources": dict(
            Counter(str(row["sample_source"]) for row in pairs)
        ),
        "records": [
            {
                "record_id": str(row["record_id"]),
                "record_type": str(row["record_type"]),
                "pair_source": None,
            }
            for row in rows
            if row["record_type"] == "parse"
        ]
        + [
            {
                "record_id": hashlib.sha256(
                    (
                        f"pair|{row['proposition_a_id']}|"
                        f"{row['proposition_b_id']}"
                    ).encode()
                ).hexdigest(),
                "record_type": "pair",
                "pair_source": str(row["sample_source"]),
            }
            for row in pairs
        ],
    }
    (output_dir / "sampling_manifest.json").write_text(
        json.dumps(sampling, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"parse_records": len(selected), "pair_records": len(pairs)}


def compile_benchmark(
    input_path: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    adjudication_path: Path,
    sampling_manifest_path: Path,
    output_path: Path,
    *,
    min_parse_records: int = 750,
    min_pair_records: int = 3_000,
    enforce_balance: bool = True,
) -> dict[str, Any]:
    input_path = input_path.resolve()
    reviews_a = _read_reviews(reviewer_a_path)
    reviews_b = _read_reviews(reviewer_b_path)
    adjudication = _read_reviews(adjudication_path, allow_incomplete=True)
    if set(reviews_a) != set(reviews_b):
        raise ValueError("Reviewer files do not contain identical record IDs")
    aliases_a = {str(row["reviewer_alias"]).strip() for row in reviews_a.values()}
    aliases_b = {str(row["reviewer_alias"]).strip() for row in reviews_b.values()}
    if len(aliases_a) != 1 or len(aliases_b) != 1 or aliases_a == aliases_b:
        raise ValueError("Two distinct, consistent reviewer aliases are required")

    source_hash = _sha256(input_path)
    if source_hash != CANONICAL_SOURCE_SHA256:
        raise ValueError(
            "Benchmark compilation requires the canonical supplied catalog"
        )
    sampling, sampling_hash = _load_sampling_manifest(
        sampling_manifest_path,
        source_hash,
        set(reviews_a),
        require_all_sources=enforce_balance,
    )
    sampling_by_id = {
        str(row["record_id"]): row
        for row in sampling["records"]
    }
    _validate_review_evidence(input_path, reviews_a, reviews_b)
    compiled: list[dict[str, Any]] = []
    for record_id in sorted(reviews_a):
        raw_a, raw_b = reviews_a[record_id], reviews_b[record_id]
        label_a = _review_label(raw_a)
        label_b = _review_label(raw_b)
        differences = sorted(
            key for key in label_a if label_a.get(key) != label_b.get(key)
        )
        if differences:
            final_raw = adjudication.get(record_id)
            if final_raw is None:
                raise ValueError(
                    f"Disagreement {record_id} requires adjudication"
                )
            adjudicator_alias = str(
                final_raw.get("reviewer_alias") or ""
            ).strip()
            if (
                not adjudicator_alias
                or adjudicator_alias in aliases_a
                or adjudicator_alias in aliases_b
            ):
                raise ValueError(
                    f"Disagreement {record_id} requires an independent "
                    "adjudicator alias"
                )
            if _review_evidence(final_raw) != _review_evidence(raw_a):
                raise ValueError(
                    f"Adjudication evidence differs for {record_id}"
                )
            final_label = _review_label(final_raw)
            final_notes = str(final_raw.get("reviewer_notes") or "").strip()
            if not final_notes:
                raise ValueError(
                    f"Adjudication notes are required for {record_id}"
                )
        else:
            final_label = label_a
            final_notes = (
                f"{next(iter(aliases_a))}: {raw_a['reviewer_notes']} | "
                f"{next(iter(aliases_b))}: {raw_b['reviewer_notes']}"
            )
        compiled.append(
            _compiled_row(
                raw_a,
                raw_b,
                source_hash,
                label_a,
                label_b,
                final_label,
                differences,
                final_notes,
                _blank_none(sampling_by_id[record_id].get("pair_source")),
                sampling_hash,
            )
        )

    _assign_benchmark_partitions(compiled)
    _validate_compiled_benchmark(
        compiled,
        min_parse_records=min_parse_records,
        min_pair_records=min_pair_records,
        enforce_balance=enforce_balance,
    )
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    db = DuckDB()
    try:
        create_and_fill(db, "benchmark_v", BENCHMARK_COLUMNS, compiled)
        db.execute(
            f"""
            COPY (
                SELECT {", ".join(BENCHMARK_COLUMNS)}
                FROM benchmark_v
                ORDER BY record_type, record_id
            ) TO '{q(output_path)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    return {
        "source_sha256": source_hash,
        "parse_records": sum(row["record_type"] == "parse" for row in compiled),
        "pair_records": sum(row["record_type"] == "pair" for row in compiled),
        "disagreements": sum(bool(row["disagreement"]) for row in compiled),
    }


def _validate_review_evidence(
    input_path: Path,
    reviews_a: dict[str, dict[str, str]],
    reviews_b: dict[str, dict[str, str]],
) -> None:
    _, _, markets, _ = load_source_markets(
        input_path,
        max_propositions=5_000,
    )
    source: dict[str, dict[str, str]] = {}
    for market in markets:
        domain = assign_domain(
            market.question,
            market.description,
            market.event_slug or "",
            market.category or "",
            market.event_id or "",
            market.tags,
        )
        for outcome in market.outcomes:
            source[outcome.clob_token_id] = {
                "question": market.question,
                "description": market.description,
                "outcome": outcome.outcome,
                "domain": domain,
            }

    for record_id in sorted(reviews_a):
        row_a = reviews_a[record_id]
        row_b = reviews_b[record_id]
        if _review_evidence(row_a) != _review_evidence(row_b):
            raise ValueError(
                f"Reviewer evidence differs for {record_id}"
            )
        record_type = str(row_a["record_type"])
        a_id = str(row_a["proposition_a_id"])
        b_id = str(row_a.get("proposition_b_id") or "")
        if a_id not in source:
            raise ValueError(
                f"Review record {record_id} references a proposition outside "
                "the canonical top-5,000 population"
            )
        expected_record_id: str
        if record_type == "parse":
            if b_id:
                raise ValueError(
                    f"Parse record {record_id} must not contain proposition B"
                )
            expected_record_id = hashlib.sha256(
                f"parse|{a_id}".encode()
            ).hexdigest()
            expected_domain = source[a_id]["domain"]
        elif record_type == "pair":
            if (
                not b_id
                or b_id not in source
                or _ordered_pair(a_id, b_id) != (a_id, b_id)
            ):
                raise ValueError(
                    f"Pair record {record_id} must contain two canonical, "
                    "stably ordered top-5,000 propositions"
                )
            expected_record_id = hashlib.sha256(
                f"pair|{a_id}|{b_id}".encode()
            ).hexdigest()
            expected_domain = (
                source[a_id]["domain"]
                if source[a_id]["domain"] == source[b_id]["domain"]
                else "cross_domain"
            )
        else:
            raise ValueError(
                f"Unsupported record type {record_type!r}"
            )
        if record_id != expected_record_id:
            raise ValueError(
                f"Review record ID does not match canonical evidence: {record_id}"
            )
        expected_evidence = {
            "domain": expected_domain,
            "question_a": source[a_id]["question"],
            "description_a": source[a_id]["description"],
            "outcome_a": source[a_id]["outcome"],
            "question_b": source[b_id]["question"] if b_id else "",
            "description_b": source[b_id]["description"] if b_id else "",
            "outcome_b": source[b_id]["outcome"] if b_id else "",
        }
        for field, expected in expected_evidence.items():
            if str(row_a.get(field) or "") != expected:
                raise ValueError(
                    f"Review evidence field {field} differs from the "
                    f"canonical source for {record_id}"
                )


def _review_evidence(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) or "")
        for field in (
            "record_id",
            "record_type",
            "domain",
            "proposition_a_id",
            "proposition_b_id",
            "question_a",
            "description_a",
            "outcome_a",
            "question_b",
            "description_b",
            "outcome_b",
        )
    )


def evaluate_build(
    out_dir: Path,
    benchmark_path: Path,
    *,
    input_hash: str | None = None,
    compute_profile: Path | None = None,
    output_path: Path | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    benchmark_path = benchmark_path.resolve()
    if not benchmark_path.is_file():
        raise ValueError(f"Benchmark does not exist: {benchmark_path}")
    existing_manifest_path = out_dir / "build_manifest.json"
    existing_manifest = (
        json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing_manifest_path.is_file()
        else {}
    )
    db = DuckDB()
    try:
        benchmark = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(benchmark_path)}')
            ORDER BY record_type, record_id
            """
        )
        propositions = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(out_dir / "propositions.parquet")}')
            ORDER BY proposition_id
            """
        )
        candidates = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(out_dir / "relation_candidates.parquet")}')
            ORDER BY proposition_a_id, proposition_b_id
            """
        )
        edges = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(out_dir / "logic_edges.parquet")}')
            ORDER BY src_node_id, dst_node_id, edge_type
            """
        )
        review_count = int(
            db.scalar(
                f"SELECT count(*) FROM read_parquet('{q(out_dir / 'review_queue.parquet')}')"
            )
            or 0
        )
    finally:
        db.close()

    source_hashes = {str(row["source_sha256"]) for row in benchmark}
    if len(source_hashes) != 1:
        raise ValueError("Benchmark must contain one source hash")
    if {
        str(row["benchmark_version"]) for row in benchmark
    } != {BENCHMARK_VERSION} or {
        str(row["schema_version"]) for row in benchmark
    } != {BENCHMARK_SCHEMA_VERSION}:
        raise ValueError("Benchmark version or schema is not supported")
    expected_input_hash = next(iter(source_hashes))
    if input_hash is None:
        if existing_manifest:
            input_hash = str(
                existing_manifest["input_hash"]
            )
    if input_hash is not None and input_hash != expected_input_hash:
        raise ValueError("Benchmark source hash does not match discovery input")

    proposition_by_id = {
        str(row["proposition_id"]): row for row in propositions
    }
    all_parse_rows = [row for row in benchmark if row["record_type"] == "parse"]
    all_pair_rows = [row for row in benchmark if row["record_type"] == "pair"]
    benchmark_complete = _benchmark_release_complete(
        all_parse_rows,
        all_pair_rows,
    )
    missing_ids = sorted(
        {
            str(row[key])
            for row in benchmark
            for key in ("proposition_a_id", "proposition_b_id")
            if row.get(key) and str(row[key]) not in proposition_by_id
        }
    )
    if missing_ids:
        raise ValueError(
            f"Benchmark contains {len(missing_ids)} propositions absent from build"
        )
    parse_rows = [
        row for row in all_parse_rows if row.get("partition") == "test"
    ]
    pair_rows = [
        row for row in all_pair_rows if row.get("partition") == "test"
    ]
    if not parse_rows or not pair_rows:
        raise ValueError("Evaluation requires an untouched test partition")

    parser = _parser_metrics(parse_rows, proposition_by_id)
    candidate_pairs = {
        _ordered_pair(
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        )
        for row in candidates
    }
    classified_count = sum(
        row.get("classification_relation") is not None for row in candidates
    )
    retrieval = _retrieval_metrics(
        pair_rows,
        candidate_pairs,
        len(candidates),
        len(propositions),
        classified_count,
    )
    prediction_rows = _prediction_rows(pair_rows, edges, candidates)
    deterministic = _prediction_metrics(
        prediction_rows,
        method="deterministic",
    )
    generative_model = _prediction_metrics(
        prediction_rows,
        method="generative_model",
    )
    nli = _prediction_metrics(prediction_rows, method="nli")
    model = _prediction_metrics(
        prediction_rows,
        method={"generative_model", "nli"},
    )
    overall = _prediction_metrics(prediction_rows)
    deterministic_accepted = [
        row
        for row in prediction_rows
        if row["published"] and row["method"] == "deterministic"
    ]
    overall_accepted = [
        row for row in prediction_rows if row["published"]
    ]
    deterministic_accepted_precision = _precision(deterministic_accepted)
    overall_accepted_precision = _precision(overall_accepted)
    complement_rows = [
        row
        for row in overall_accepted
        if row["predicted"] == "complement"
    ]
    complement_precision = _precision(complement_rows)
    unsupported_rate = (
        sum(
            bool(row["unsupported_assumption"])
            for row in overall_accepted
            if row["method"] in {"generative_model", "nli"}
        )
        / max(
            1,
            sum(
                row["method"] in {"generative_model", "nli"}
                for row in overall_accepted
            ),
        )
    )
    provenance_failures = _provenance_failures(edges)
    compute = _compute_evaluation_metrics(
        out_dir,
        compute_profile,
        run_metadata=run_metadata,
    )
    execution = dict(
        (run_metadata or {}).get("stats")
        or existing_manifest.get("stats")
        or {}
    )
    incremental = dict(execution.get("incremental") or {})
    solver_execution = dict(execution.get("solver") or {})
    validation = dict(
        (run_metadata or {}).get("validation")
        or {
            "offline": (existing_manifest.get("cache") or {}).get(
                "offline",
                False,
            ),
            "max_propositions": (
                existing_manifest.get("limits") or {}
            ).get("max_propositions"),
        }
    )
    offline_replay = bool(validation.get("offline"))
    replay_without_recompute = (
        offline_replay
        and int(incremental.get("embedding_vectors_recomputed", 1)) == 0
        and int(solver_execution.get("components_recomputed", 1)) == 0
    )
    execution_plan = dict(incremental.get("execution_plan") or {})
    execution_plan_path = out_dir / "state" / "execution_plan.parquet"
    verified_execution_plan: dict[str, Any] = {}
    if execution_plan_path.is_file():
        plan_db = DuckDB()
        try:
            plan_rows = plan_db.rows(
                f"""
                SELECT *
                FROM read_parquet('{q(execution_plan_path)}')
                ORDER BY stage, unit_type, unit_id
                """
            )
        finally:
            plan_db.close()
        verified_execution_plan = ExecutionPlan.from_rows(
            incremental=bool(
                incremental.get("enabled")
                or incremental.get("offline_state_replay")
            ),
            rows=plan_rows,
        ).manifest()
    affected_components_only = bool(
        verified_execution_plan.get("affected_only_verified")
        and verified_execution_plan.get("hash") == execution_plan.get("hash")
        and not verified_execution_plan.get("verification_errors")
    )
    rule_execution = dict(execution.get("rules") or {})
    selected_propositions = int(execution.get("tokens", 0))
    local_m4_completion = (
        selected_propositions in {5_000, 20_000}
        and int(validation.get("max_propositions") or 0)
        == selected_propositions
    )
    inference_metadata = dict(
        (run_metadata or {}).get("inference")
        or existing_manifest.get("inference")
        or {}
    )
    model_manifest_data = _read_optional_json(out_dir / "model_manifest.json")
    model_profile_data = _read_optional_json(out_dir / "model_profile.json")
    model_metadata = dict(
        (run_metadata or {}).get("models")
        or existing_manifest.get("models")
        or {}
    )
    observed_models = {
        str(value)
        for role in ("parse", "classify")
        for value in (model_metadata.get(role) or {}).get("observed", [])
    }
    requested_models = {
        str((model_metadata.get(role) or {}).get("requested") or "")
        for role in ("parse", "classify")
    }
    gates = {
        "deterministic_precision": (
            deterministic_accepted_precision is not None
            and deterministic_accepted_precision >= 0.99
        ),
        "overall_precision": (
            overall_accepted_precision is not None
            and overall_accepted_precision >= 0.97
        ),
        "candidate_recall": (
            retrieval["candidate_recall"] is not None
            and retrieval["candidate_recall"] >= 0.98
        ),
        "complement_precision": (
            complement_precision is not None and complement_precision >= 0.995
        ),
        "unsupported_assumption_rate": unsupported_rate < 0.01,
        "complete_provenance": provenance_failures == 0,
        "benchmark_complete": benchmark_complete,
        "deterministic_cached_replay": replay_without_recompute,
        "affected_incremental_components_only": affected_components_only,
        "no_unbenchmarked_rule_override": not bool(
            rule_execution.get("allow_unbenchmarked_rules")
        ),
        "local_m4_completion": local_m4_completion,
        "self_hosted_runtime": (
            inference_metadata.get("runtime") in {"llama.cpp", "vllm"}
            and str(inference_metadata.get("origin") or "").startswith(
                ("http://", "https://")
            )
        ),
        "approved_open_license": (
            model_manifest_data.get("license") == "Apache-2.0"
        ),
        "matching_model_profile": (
            bool(inference_metadata.get("profiled"))
            and model_profile_data.get("profile_id")
            == inference_metadata.get("model_profile_id")
        ),
        "model_id_coverage": (
            bool(observed_models)
            and observed_models.issubset(requested_models)
            and all(str(row.get("parser_model") or "") for row in propositions)
        ),
        "structured_output_validity": (
            float(model_profile_data.get("structured_output_validity") or 0.0)
            >= 0.999
        ),
        "no_proprietary_cache_lineage": (
            inference_metadata.get("proprietary_cache_lineage") is False
        ),
        "runtime_conformance": (
            inference_metadata.get("runtime") in {"llama.cpp", "vllm"}
            and inference_metadata.get("runtime_version")
            not in {None, "", "unreported"}
            and int(model_manifest_data.get("context_length") or 0) > 1
            and bool(inference_metadata.get("fingerprints"))
        ),
    }
    failed_predictions = [
        row for row in prediction_rows if not row["correct"]
    ]
    parser_attributable = _parser_attributable_failures(
        failed_predictions,
        parse_rows,
        proposition_by_id,
    )
    if all(gates.values()):
        decision = "READY_TO_SCALE"
    elif failed_predictions and parser_attributable * 2 >= len(failed_predictions):
        decision = "NEEDS_PARSER_WORK"
    elif not gates["candidate_recall"]:
        decision = "NEEDS_RETRIEVAL_WORK"
    else:
        decision = "NEEDS_RELATION_MODEL_WORK"

    failure_categories = Counter()
    for row in failed_predictions:
        failure_categories[
            "false_positive" if row["expected"] in {"unrelated", "uncertain"} else "wrong_or_missing_relation"
        ] += 1
    if parser_attributable:
        failure_categories["parser_attributable"] = parser_attributable
    report = {
        "benchmark": {
            "version": BENCHMARK_VERSION,
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "source_sha256": expected_input_hash,
            "hash": _sha256(benchmark_path),
            "parse_records": len(parse_rows),
            "pair_records": len(pair_rows),
            "total_parse_records": len(all_parse_rows),
            "total_pair_records": len(all_pair_rows),
            "partition": "test",
        },
        "metrics": {
            "parser": parser,
            "retrieval": retrieval,
            "deterministic": deterministic,
            "generative_model": generative_model,
            "nli": nli,
            "model": model,
            "overall": overall,
            "deterministic_accepted_edge_precision": (
                deterministic_accepted_precision
            ),
            "overall_accepted_edge_precision": overall_accepted_precision,
            "complement_precision": complement_precision,
            "unsupported_assumption_rate": unsupported_rate,
            "review_rate": review_count / max(1, len(candidates)),
            "provenance_failures": provenance_failures,
            "compute": compute,
        },
        "failure_categories": dict(failure_categories.most_common()),
        "execution": execution,
        "gates": gates,
        "exit_decision": decision,
        "passed": decision == "READY_TO_SCALE",
    }
    destination = (output_path or out_dir / "evaluation_report.json").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    benchmark_destination = out_dir / "benchmark.parquet"
    if benchmark_destination.resolve() != benchmark_path:
        shutil.copyfile(benchmark_path, benchmark_destination)
    compute_destination = out_dir / "compute_profile.json"
    if (
        compute_profile is not None
        and compute_destination.resolve() != compute_profile.resolve()
    ):
        shutil.copyfile(compute_profile.resolve(), compute_destination)
    if existing_manifest:
        manifest = dict(existing_manifest)
        artifacts = list(manifest.get("artifacts") or [])
        if "benchmark.parquet" not in artifacts:
            artifacts.append("benchmark.parquet")
        canonical_report = out_dir / "evaluation_report.json"
        if (
            destination == canonical_report.resolve()
            and "evaluation_report.json" not in artifacts
        ):
            artifacts.append("evaluation_report.json")
        artifact_hashes = dict(manifest.get("artifact_hashes") or {})
        artifact_hashes["benchmark.parquet"] = _sha256(
            benchmark_destination
        )
        if compute_profile is not None:
            if "compute_profile.json" not in artifacts:
                artifacts.append("compute_profile.json")
            artifact_hashes["compute_profile.json"] = _sha256(
                compute_destination
            )
        stats = dict(manifest.get("stats") or {})
        stats["evaluation_exit_decision"] = decision
        manifest.update(
            {
                "artifacts": artifacts,
                "artifact_hashes": artifact_hashes,
                "benchmark": {
                    "path": str(benchmark_path),
                    "hash": _sha256(benchmark_path),
                },
                "compute": compute,
                "evaluation": {
                    "path": str(destination),
                    "hash": _sha256(destination),
                    "exit_decision": decision,
                },
                "stats": stats,
            }
        )
        _write_json_atomic(existing_manifest_path, manifest)
    return report


def _stratified_propositions(
    rows: list[dict[str, Any]],
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    by_domain: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        by_domain[str(row["domain"])][str(row["market_id"])].append(row)
    base, remainder = divmod(count, len(DOMAINS))
    selected: list[dict[str, Any]] = []
    for index, domain in enumerate(DOMAINS):
        needed = base + (1 if index < remainder else 0)
        ranked = sorted(
            (
                market_rows
                for market_rows in by_domain[domain].values()
                if len(market_rows) == 2
            ),
            key=lambda market_rows: _stable_hash(
                seed, str(market_rows[0]["market_id"])
            ),
        )
        domain_rows: list[dict[str, Any]] = []
        for market_rows in ranked:
            if len(domain_rows) + len(market_rows) > needed:
                continue
            domain_rows.extend(market_rows)
            if len(domain_rows) == needed:
                break
        if len(domain_rows) < needed:
            raise ValueError(
                f"Domain {domain} has only {len(domain_rows)} eligible propositions "
                "in complete binary markets; "
                f"{needed} required"
            )
        selected.extend(domain_rows)
    return selected


def _benchmark_pairs(
    propositions: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    count: int,
    *,
    seed: int,
) -> list[dict[str, Any]]:
    candidate_rows = []
    candidate_set: set[tuple[str, str]] = set()
    for row in candidates:
        a_id, b_id = _ordered_pair(
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        )
        candidate_set.add((a_id, b_id))
        reasons = set(row.get("candidate_reasons") or [])
        if row.get("deterministic_relation") == "complement":
            source = "candidate"
        elif "embedding_top_k" in reasons:
            source = "semantic_near_miss"
        else:
            source = "structured_near_miss"
        candidate_rows.append(
            {
                "proposition_a_id": a_id,
                "proposition_b_id": b_id,
                "sample_source": source,
            }
        )
    ids = sorted(propositions)
    noncandidates = [
        {
            "proposition_a_id": a_id,
            "proposition_b_id": b_id,
            "sample_source": "noncandidate",
        }
        for a_id, b_id in combinations(ids, 2)
        if (a_id, b_id) not in candidate_set
    ]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in [*candidate_rows, *noncandidates]:
        groups[str(row["sample_source"])].append(row)
    for source, group in groups.items():
        group.sort(
            key=lambda row: _stable_hash(
                seed,
                source,
                str(row["proposition_a_id"]),
                str(row["proposition_b_id"]),
            )
        )
    selected = groups["candidate"][: min(200, count)]
    groups["candidate"] = groups["candidate"][len(selected) :]
    while len(selected) < count:
        progressed = False
        for source in (
            "candidate",
            "semantic_near_miss",
            "structured_near_miss",
            "noncandidate",
        ):
            if groups[source]:
                selected.append(groups[source].pop(0))
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            break
    if len(selected) < count:
        raise ValueError(f"Only {len(selected)} benchmark pairs are available")
    return sorted(
        selected,
        key=lambda row: (
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        ),
    )


def _parse_review_row(row: dict[str, Any]) -> dict[str, str]:
    proposition_id = str(row["proposition_id"])
    return {
        **{field: "" for field in REVIEW_FIELDS},
        "record_id": hashlib.sha256(f"parse|{proposition_id}".encode()).hexdigest(),
        "record_type": "parse",
        "domain": str(row["domain"]),
        "proposition_a_id": proposition_id,
        "question_a": str(row["question"]),
        "description_a": str(row.get("description") or ""),
        "outcome_a": str(row["outcome"]),
    }


def _pair_review_row(
    row: dict[str, Any],
    propositions: dict[str, dict[str, Any]],
) -> dict[str, str]:
    a_id = str(row["proposition_a_id"])
    b_id = str(row["proposition_b_id"])
    a, b = propositions[a_id], propositions[b_id]
    domain = (
        str(a["domain"])
        if a["domain"] == b["domain"]
        else "cross_domain"
    )
    return {
        **{field: "" for field in REVIEW_FIELDS},
        "record_id": hashlib.sha256(f"pair|{a_id}|{b_id}".encode()).hexdigest(),
        "record_type": "pair",
        "domain": domain,
        "proposition_a_id": a_id,
        "proposition_b_id": b_id,
        "question_a": str(a["question"]),
        "description_a": str(a.get("description") or ""),
        "outcome_a": str(a["outcome"]),
        "question_b": str(b["question"]),
        "description_b": str(b.get("description") or ""),
        "outcome_b": str(b["outcome"]),
    }


def _read_reviews(
    path: Path,
    *,
    allow_incomplete: bool = False,
) -> dict[str, dict[str, str]]:
    with path.resolve().open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fieldnames = set(reader.fieldnames or ())
    missing = set(REVIEW_FIELDS) - fieldnames
    if missing:
        raise ValueError(
            f"{path} is missing review columns: {', '.join(sorted(missing))}"
        )
    result = {}
    for row in rows:
        record_id = str(row["record_id"])
        if record_id in result:
            raise ValueError(f"Duplicate review record {record_id}")
        if not allow_incomplete:
            if not str(row.get("reviewer_alias") or "").strip():
                raise ValueError(f"Reviewer alias is required for {record_id}")
            if not str(row.get("reviewer_notes") or "").strip():
                raise ValueError(f"Reviewer notes are required for {record_id}")
            _review_label(row)
        result[record_id] = dict(row)
    return result


def _review_label(row: dict[str, str]) -> dict[str, Any]:
    record_type = str(row["record_type"])
    if record_type == "pair":
        relation = str(row.get("expected_relation") or "").strip()
        if relation not in RELATIONS:
            raise ValueError(
                f"Unsupported or missing relation for {row['record_id']}: {relation!r}"
            )
        unsupported = _required_bool(
            row.get("unsupported_assumption"),
            row["record_id"],
        )
        return {
            "expected_relation": relation,
            "unsupported_assumption": unsupported,
        }
    if record_type != "parse":
        raise ValueError(f"Unsupported record type {record_type!r}")
    try:
        subjects = json.loads(str(row["expected_subjects_json"]))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid expected_subjects_json for {row['record_id']}"
        ) from exc
    if not isinstance(subjects, list) or not subjects or not all(
        isinstance(item, str) and item.strip() for item in subjects
    ):
        raise ValueError(
            f"expected_subjects_json for {row['record_id']} must be a nonempty string list"
        )
    polarity = str(row.get("expected_polarity") or "").strip()
    if polarity not in {"positive", "negative"}:
        raise ValueError(f"Expected polarity is required for {row['record_id']}")
    threshold_text = str(row.get("expected_threshold") or "").strip()
    return {
        "expected_subjects": sorted(set(subjects)),
        "expected_predicate": _blank_none(row.get("expected_predicate")),
        "expected_object": _blank_none(row.get("expected_object")),
        "expected_operator": _blank_none(row.get("expected_operator")),
        "expected_threshold": (
            float(threshold_text) if threshold_text else None
        ),
        "expected_unit": _blank_none(row.get("expected_unit")),
        "expected_time_start": _blank_none(row.get("expected_time_start")),
        "expected_time_end": _blank_none(row.get("expected_time_end")),
        "expected_competition": _blank_none(row.get("expected_competition")),
        "expected_event_scope": _blank_none(row.get("expected_event_scope")),
        "expected_jurisdiction": _blank_none(row.get("expected_jurisdiction")),
        "expected_polarity": polarity,
    }


def _compiled_row(
    raw_a: dict[str, str],
    raw_b: dict[str, str],
    source_hash: str,
    label_a: dict[str, Any],
    label_b: dict[str, Any],
    final_label: dict[str, Any],
    differences: list[str],
    final_notes: str,
    pair_source: str | None,
    sampling_manifest_hash: str,
) -> dict[str, Any]:
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "source_sha256": source_hash,
        "sampling_manifest_sha256": sampling_manifest_hash,
        "partition": None,
        "pair_source": pair_source,
        "record_id": raw_a["record_id"],
        "record_type": raw_a["record_type"],
        "domain": raw_a["domain"],
        "proposition_a_id": raw_a["proposition_a_id"],
        "proposition_b_id": _blank_none(raw_a.get("proposition_b_id")),
        "expected_subjects": final_label.get("expected_subjects"),
        "expected_predicate": final_label.get("expected_predicate"),
        "expected_object": final_label.get("expected_object"),
        "expected_operator": final_label.get("expected_operator"),
        "expected_threshold": final_label.get("expected_threshold"),
        "expected_unit": final_label.get("expected_unit"),
        "expected_time_start": final_label.get("expected_time_start"),
        "expected_time_end": final_label.get("expected_time_end"),
        "expected_competition": final_label.get("expected_competition"),
        "expected_event_scope": final_label.get("expected_event_scope"),
        "expected_jurisdiction": final_label.get("expected_jurisdiction"),
        "expected_polarity": final_label.get("expected_polarity"),
        "expected_relation": final_label.get("expected_relation"),
        "unsupported_assumption": final_label.get("unsupported_assumption"),
        "reviewer_a_alias": raw_a["reviewer_alias"],
        "reviewer_a_label": json.dumps(label_a, sort_keys=True),
        "reviewer_a_notes": raw_a["reviewer_notes"],
        "reviewer_b_alias": raw_b["reviewer_alias"],
        "reviewer_b_label": json.dumps(label_b, sort_keys=True),
        "reviewer_b_notes": raw_b["reviewer_notes"],
        "disagreement": bool(differences),
        "disagreement_fields": differences,
        "final_notes": final_notes,
    }


def _load_sampling_manifest(
    path: Path,
    source_hash: str,
    record_ids: set[str],
    *,
    require_all_sources: bool,
) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    try:
        manifest = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid sampling manifest {resolved}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Sampling manifest must be a JSON object")
    if (
        manifest.get("benchmark_version") != BENCHMARK_VERSION
        or manifest.get("source_sha256") != source_hash
    ):
        raise ValueError("Sampling manifest version or source hash does not match")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("Sampling manifest must contain per-record metadata")
    indexed: dict[str, dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, dict) or not raw.get("record_id"):
            raise ValueError("Sampling manifest contains an invalid record")
        record_id = str(raw["record_id"])
        if record_id in indexed:
            raise ValueError("Sampling manifest contains duplicate record IDs")
        indexed[record_id] = raw
    if set(indexed) != record_ids:
        raise ValueError("Sampling manifest record IDs do not match reviewer files")
    pair_sources = {
        str(row.get("pair_source") or "")
        for row in indexed.values()
        if row.get("record_type") == "pair"
    }
    required_sources = {
        "candidate",
        "semantic_near_miss",
        "structured_near_miss",
        "noncandidate",
    }
    if require_all_sources and pair_sources != required_sources:
        raise ValueError("Sampling manifest does not contain every pair source")
    return manifest, _sha256(resolved)


def _assign_benchmark_partitions(rows: list[dict[str, Any]]) -> None:
    strata: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["record_type"] == "parse":
            stratum = ("parse", str(row["domain"]))
        else:
            stratum = (
                "pair",
                str(row["expected_relation"]),
                str(row["pair_source"]),
                str(bool(row["unsupported_assumption"])),
            )
        strata[stratum].append(row)
    for stratum, members in sorted(strata.items()):
        members.sort(
            key=lambda row: hashlib.sha256(
                (
                    BENCHMARK_VERSION
                    + "|"
                    + "|".join(stratum)
                    + "|"
                    + str(row["record_id"])
                ).encode()
            ).hexdigest()
        )
        count = len(members)
        development_end = round(count * 0.4)
        calibration_end = development_end + round(count * 0.3)
        for index, row in enumerate(members):
            row["partition"] = (
                "development"
                if index < development_end
                else ("calibration" if index < calibration_end else "test")
            )


def _validate_compiled_benchmark(
    rows: list[dict[str, Any]],
    *,
    min_parse_records: int,
    min_pair_records: int,
    enforce_balance: bool,
) -> None:
    parse_rows = [row for row in rows if row["record_type"] == "parse"]
    pair_rows = [row for row in rows if row["record_type"] == "pair"]
    if len(parse_rows) < min_parse_records or len(pair_rows) < min_pair_records:
        raise ValueError(
            f"Benchmark requires at least {min_parse_records} parses and "
            f"{min_pair_records} pairs"
        )
    parse_ids = {str(row["proposition_a_id"]) for row in parse_rows}
    missing_endpoints = {
        str(row[key])
        for row in pair_rows
        for key in ("proposition_a_id", "proposition_b_id")
        if str(row[key]) not in parse_ids
    }
    if missing_endpoints:
        raise ValueError("Every pair endpoint must have a reviewed parse")
    if not enforce_balance:
        return
    domain_counts = Counter(str(row["domain"]) for row in parse_rows)
    if any(domain_counts[domain] < 150 for domain in DOMAINS):
        raise ValueError("Benchmark requires at least 150 parses per target domain")
    relation_counts = Counter(str(row["expected_relation"]) for row in pair_rows)
    positives = sum(relation_counts[relation] for relation in POSITIVE_RELATIONS)
    ratio = positives / len(pair_rows)
    if not 0.4 <= ratio <= 0.6:
        raise ValueError("Benchmark positive/negative balance must be 40–60%")
    if any(relation_counts[relation] < 100 for relation in RELATIONS):
        raise ValueError("Every relation requires at least 100 benchmark records")
    if relation_counts["complement"] < 300:
        raise ValueError("Benchmark requires at least 300 complement records")
    partitions = {"development", "calibration", "test"}
    if {str(row["partition"]) for row in rows} != partitions:
        raise ValueError("Benchmark must contain development, calibration, and test")
    required_sources = {
        "candidate",
        "semantic_near_miss",
        "structured_near_miss",
        "noncandidate",
    }
    for partition in partitions:
        partition_parse_domains = {
            str(row["domain"])
            for row in parse_rows
            if row["partition"] == partition
        }
        if partition_parse_domains != set(DOMAINS):
            raise ValueError(
                f"Every parse domain must appear in the {partition} partition"
            )
        partition_relations = {
            str(row["expected_relation"])
            for row in pair_rows
            if row["partition"] == partition
        }
        if partition_relations != RELATIONS:
            raise ValueError(
                f"Every relation must appear in the {partition} partition"
            )
        observed = {
            str(row["pair_source"])
            for row in pair_rows
            if row["partition"] == partition
        }
        if observed != required_sources:
            raise ValueError(
                f"Every pair source must appear in the {partition} partition"
            )


def _benchmark_release_complete(
    parse_rows: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
) -> bool:
    if len(parse_rows) < 750 or len(pair_rows) < 3_000:
        return False
    domain_counts = Counter(str(row["domain"]) for row in parse_rows)
    if any(domain_counts[domain] < 150 for domain in DOMAINS):
        return False
    relation_counts = Counter(
        str(row["expected_relation"]) for row in pair_rows
    )
    positives = sum(
        relation_counts[relation] for relation in POSITIVE_RELATIONS
    )
    if not 0.4 <= positives / len(pair_rows) <= 0.6:
        return False
    if any(relation_counts[relation] < 100 for relation in RELATIONS):
        return False
    if relation_counts["complement"] < 300:
        return False
    if {str(row["partition"]) for row in [*parse_rows, *pair_rows]} != {
        "development",
        "calibration",
        "test",
    }:
        return False
    required_sources = {
        "candidate",
        "semantic_near_miss",
        "structured_near_miss",
        "noncandidate",
    }
    for partition in ("development", "calibration", "test"):
        if {
            str(row["domain"])
            for row in parse_rows
            if row["partition"] == partition
        } != set(DOMAINS):
            return False
        if {
            str(row["expected_relation"])
            for row in pair_rows
            if row["partition"] == partition
        } != RELATIONS:
            return False
        if {
            str(row["pair_source"])
            for row in pair_rows
            if row["partition"] == partition
        } != required_sources:
            return False
    return all(
        row.get("reviewer_a_alias")
        and row.get("reviewer_b_alias")
        and row.get("reviewer_a_notes")
        and row.get("reviewer_b_notes")
        and row.get("final_notes")
        for row in [*parse_rows, *pair_rows]
    )


def _parser_metrics(
    benchmark: list[dict[str, Any]],
    propositions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mapping = {
        "subjects": ("expected_subjects", "subject"),
        "predicate": ("expected_predicate", "predicate"),
        "object": ("expected_object", "object"),
        "operator": ("expected_operator", "operator"),
        "threshold": ("expected_threshold", "threshold"),
        "unit": ("expected_unit", "unit"),
        "time_start": ("expected_time_start", "time_start"),
        "time_end": ("expected_time_end", "time_end"),
        "competition": ("expected_competition", "competition"),
        "event_scope": ("expected_event_scope", "event_scope"),
        "jurisdiction": ("expected_jurisdiction", "jurisdiction"),
        "polarity": ("expected_polarity", "polarity"),
    }
    correct = Counter()
    totals = Counter()
    entity_tp = entity_fp = entity_fn = 0
    for expected in benchmark:
        actual = propositions[str(expected["proposition_a_id"])]
        for metric, (expected_key, actual_key) in mapping.items():
            totals[metric] += 1
            if metric == "subjects":
                expected_set = set(expected[expected_key] or [])
                actual_set = set(actual[actual_key] or [])
                correct[metric] += expected_set == actual_set
                entity_tp += len(expected_set & actual_set)
                entity_fp += len(actual_set - expected_set)
                entity_fn += len(expected_set - actual_set)
            elif _metric_equal(expected[expected_key], actual[actual_key]):
                correct[metric] += 1
    field_accuracy = {
        metric: correct[metric] / totals[metric] for metric in mapping
    }
    entity_precision = entity_tp / max(1, entity_tp + entity_fp)
    entity_recall = entity_tp / max(1, entity_tp + entity_fn)
    return {
        "records": len(benchmark),
        "field_accuracy": field_accuracy,
        "entity_precision": entity_precision,
        "entity_recall": entity_recall,
        "entity_f1": _f1(entity_precision, entity_recall),
    }


def _retrieval_metrics(
    benchmark: list[dict[str, Any]],
    candidate_pairs: set[tuple[str, str]],
    candidate_count: int,
    proposition_count: int,
    classified_count: int,
) -> dict[str, Any]:
    positive = [
        row for row in benchmark if row["expected_relation"] in POSITIVE_RELATIONS
    ]
    found = sum(
        _ordered_pair(
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        )
        in candidate_pairs
        for row in positive
    )
    by_domain: dict[str, list[bool]] = defaultdict(list)
    by_relation: dict[str, list[bool]] = defaultdict(list)
    for row in positive:
        present = (
            _ordered_pair(
                str(row["proposition_a_id"]),
                str(row["proposition_b_id"]),
            )
            in candidate_pairs
        )
        by_domain[str(row["domain"])].append(present)
        by_relation[str(row["expected_relation"])].append(present)
    return {
        "candidate_recall": found / len(positive) if positive else None,
        "positive_pairs": len(positive),
        "candidates_per_proposition": candidate_count / max(1, proposition_count),
        "model_calls_avoided": max(
            0,
            proposition_count * (proposition_count - 1) // 2 - classified_count,
        ),
        "classified_pairs": classified_count,
        "recall_by_domain": {
            key: sum(values) / len(values) for key, values in sorted(by_domain.items())
        },
        "recall_by_relation": {
            key: sum(values) / len(values) for key, values in sorted(by_relation.items())
        },
    }


def _prediction_rows(
    benchmark: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edge_by_pair = {
        _ordered_pair(
            str(row["src_node_id"]),
            str(row["dst_node_id"]),
        ): row
        for row in edges
    }
    candidate_by_pair = {
        _ordered_pair(
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        ): row
        for row in candidates
    }
    rows = []
    for expected in benchmark:
        a_id, b_id = str(expected["proposition_a_id"]), str(expected["proposition_b_id"])
        pair = _ordered_pair(a_id, b_id)
        edge = edge_by_pair.get(pair)
        if edge is None:
            candidate = candidate_by_pair.get(pair)
            if candidate and candidate.get("classification_relation"):
                predicted = str(candidate["classification_relation"])
                method = str(
                    candidate.get("discovery_method")
                    or "generative_model"
                )
                confidence = float(
                    candidate.get("classification_confidence") or 0.0
                )
            else:
                predicted = None
                method = None
                confidence = 0.0
            published = False
        else:
            predicted = str(edge["edge_type"])
            if predicted == "implies":
                predicted = (
                    "A_implies_B"
                    if str(edge["src_node_id"]) == a_id
                    else "B_implies_A"
                )
            method = str(edge["discovery_method"])
            confidence = float(edge["confidence"])
            published = True
        expected_relation = str(expected["expected_relation"])
        rows.append(
            {
                "a_id": a_id,
                "b_id": b_id,
                "expected": expected_relation,
                "predicted": predicted,
                "method": method,
                "confidence": confidence,
                "correct": predicted == expected_relation,
                "published": published,
                "unsupported_assumption": bool(expected["unsupported_assumption"]),
            }
        )
    return rows


def _prediction_metrics(
    rows: list[dict[str, Any]],
    *,
    method: str | set[str] | None = None,
) -> dict[str, Any]:
    methods = {method} if isinstance(method, str) else method
    scored = [
        (
            row
            if methods is None or row["method"] in methods
            else {
                **row,
                "predicted": None,
                "confidence": 0.0,
                "correct": False,
            }
        )
        for row in rows
    ]
    accepted = [row for row in scored if row["predicted"] is not None]
    true_positive = sum(
        row["correct"] and row["expected"] in POSITIVE_RELATIONS for row in scored
    )
    expected_positive = sum(
        row["expected"] in POSITIVE_RELATIONS for row in scored
    )
    precision = _precision(accepted)
    recall = true_positive / expected_positive if expected_positive else None
    ece, brier = _calibration(accepted)
    by_relation = {}
    for relation in sorted(RELATIONS):
        relation_rows = [row for row in scored if row["expected"] == relation]
        predicted_rows = [row for row in scored if row["predicted"] == relation]
        relation_precision = _precision(predicted_rows)
        relation_recall = (
            sum(row["correct"] for row in relation_rows) / len(relation_rows)
            if relation_rows
            else None
        )
        by_relation[relation] = {
            "precision": relation_precision,
            "recall": relation_recall,
            "f1": (
                _f1(relation_precision, relation_recall)
                if relation_precision is not None and relation_recall is not None
                else None
            ),
            "support": len(relation_rows),
        }
    return {
        "accepted": len(accepted),
        "precision": precision,
        "recall": recall,
        "f1": (
            _f1(precision, recall)
            if precision is not None and recall is not None
            else None
        ),
        "ece_10_bin": ece,
        "brier_score": brier,
        "by_relation": by_relation,
    }


def _parser_attributable_failures(
    failures: list[dict[str, Any]],
    parse_benchmark: list[dict[str, Any]],
    propositions: dict[str, dict[str, Any]],
) -> int:
    expected_by_id = {
        str(row["proposition_a_id"]): row for row in parse_benchmark
    }
    count = 0
    for failure in failures:
        attributable = False
        for proposition_id in (failure["a_id"], failure["b_id"]):
            expected = expected_by_id.get(str(proposition_id))
            if expected is None:
                continue
            actual = propositions[str(proposition_id)]
            if set(expected["expected_subjects"] or []) != set(actual["subject"] or []):
                attributable = True
                break
            for key in (
                "predicate",
                "object",
                "operator",
                "threshold",
                "unit",
                "time_start",
                "time_end",
                "competition",
                "event_scope",
                "jurisdiction",
                "polarity",
            ):
                if not _metric_equal(expected[f"expected_{key}"], actual[key]):
                    attributable = True
                    break
        count += attributable
    return count


def _provenance_failures(edges: list[dict[str, Any]]) -> int:
    failures = 0
    for edge in edges:
        method = edge.get("discovery_method")
        if not edge.get("explanation") or edge.get("assumptions") is None:
            failures += 1
        elif method not in {"deterministic", "generative_model", "nli"}:
            failures += 1
        elif method == "deterministic" and (
            not edge.get("rule_version") or not edge.get("rule_id")
        ):
            failures += 1
        elif method in {"generative_model", "nli"} and (
            not edge.get("model_version")
            or not edge.get("prompt_version")
            or not edge.get("inference_fingerprint")
            or not edge.get("model_profile_id")
        ):
            failures += 1
        elif not edge.get("proposal_id") or not edge.get("solver_component_id"):
            failures += 1
    return failures


def _compute_evaluation_metrics(
    out_dir: Path,
    compute_profile: Path | None,
    *,
    run_metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    manifest = _read_optional_json(out_dir / "build_manifest.json")
    existing = (
        (run_metadata or {}).get("compute")
        or manifest.get("compute")
    )
    if compute_profile is None:
        return dict(existing) if isinstance(existing, dict) else None
    profile = _read_optional_json(compute_profile.resolve())
    hardware_rate = float(profile.get("hardware_hour_usd") or 0.0)
    load_watts = profile.get("load_watts")
    electricity_rate = profile.get("electricity_usd_per_kwh")
    timings = (
        (run_metadata or {}).get("stage_timings")
        or manifest.get("stage_timings")
        or {}
    )
    seconds = sum(
        float(timings.get(stage, 0.0))
        for stage in ("parse_propositions", "score_nli", "classify_pairs")
    )
    hours = seconds / 3600.0
    energy = (
        float(load_watts) * hours / 1000.0
        if load_watts is not None
        else None
    )
    electricity = (
        energy * float(electricity_rate)
        if energy is not None and electricity_rate is not None
        else None
    )
    usage = (
        (run_metadata or {}).get("usage")
        or manifest.get("usage")
        or {}
    )
    stats = (
        (run_metadata or {}).get("stats")
        or manifest.get("stats")
        or {}
    )
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(
        usage.get("total_tokens")
        or input_tokens + output_tokens
    )
    return {
        "profile_hash": _sha256(compute_profile.resolve()),
        "currency": str(profile.get("currency") or "USD"),
        "model_stage_seconds": seconds,
        "model_stage_hours": hours,
        "estimated_energy_kwh": energy,
        "hardware_cost": hours * hardware_rate,
        "electricity_cost": electricity,
        "total_compute_cost": hours * hardware_rate + (electricity or 0.0),
        "peak_rss_mb": stats.get("peak_rss_mb"),
        "current_request_input_tokens": input_tokens,
        "current_request_output_tokens": output_tokens,
        "current_request_tokens": total_tokens,
        "tokens_per_second": total_tokens / seconds if seconds > 0 else None,
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _metric_equal(left: object, right: object) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        if left is None or right is None:
            return left is right
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    if hasattr(left, "isoformat"):
        left = left.isoformat()  # type: ignore[union-attr]
    if hasattr(right, "isoformat"):
        right = right.isoformat()  # type: ignore[union-attr]
    return str(left) == str(right) if left is not None and right is not None else left is right


def _required_bool(value: object, record_id: str) -> bool:
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Boolean label is required for {record_id}")


def _blank_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _ordered_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _stable_hash(seed: int, *parts: str) -> str:
    return hashlib.sha256(
        "|".join((str(seed), *parts)).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
