from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
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
from ._discovery.cache import JsonCache, cache_entry, cache_error
from ._discovery.candidates import (
    candidate_sort_key as _candidate_sort_key,
    generate_candidates as _generate_candidates_bounded,
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
from ._discovery.relations import (
    SEMANTIC_KEYS as _SEMANTIC_KEYS,
    deterministic_relation as _deterministic_relation,
    hashable as _hashable,
    is_winner_proposition as _is_winner_proposition,
    normalize_text as _normalize_text,
    proposition_signature as _proposition_signature,
    stage_rank as _stage_rank,
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


PARSE_PROMPT_VERSION = "proposition-parse-v1"
CLASSIFY_PROMPT_VERSION = "relation-classify-v1"
RULE_VERSION = "discovery-rules-v1"

DISCOVERY_PARQUET_ARTIFACTS = (
    "nodes.parquet",
    "market_groups.parquet",
    "propositions.parquet",
    "relation_candidates.parquet",
    "logic_edges.parquet",
    "conditional_edges.parquet",
    "review_queue.parquet",
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
    "jurisdiction_original": "VARCHAR",
    "jurisdiction": "VARCHAR",
    "polarity": "VARCHAR",
    "parse_confidence": "DOUBLE",
    "parse_status": "VARCHAR",
    "parser_model": "VARCHAR",
    "prompt_version": "VARCHAR",
    "source_format": "VARCHAR",
}

CANDIDATE_COLUMNS = {
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "candidate_reasons": "VARCHAR[]",
    "embedding_similarity": "DOUBLE",
    "embedding_rank": "INTEGER",
    "deterministic_relation": "VARCHAR",
    "classification_relation": "VARCHAR",
    "classification_confidence": "DOUBLE",
    "explanation": "VARCHAR",
    "assumptions": "VARCHAR[]",
    "requires_review": "BOOLEAN",
    "status": "VARCHAR",
    "discovery_method": "VARCHAR",
    "model_version": "VARCHAR",
    "prompt_version": "VARCHAR",
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
Use the outcome string exactly as supplied. Normalize dates, numbers, and units.
Use null for information that is absent or not supported by the schema; never invent it.
For Yes/No markets, set No to negative polarity. Return every market and outcome exactly once."""

_CLASSIFY_PROMPT = """Classify each proposition pair using only the supplied facts.
Allowed relations are equivalent, A_implies_B, B_implies_A, mutually_exclusive,
complement, compatible, unrelated, and uncertain. State assumptions explicitly.
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
    config.validate()
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"Input parquet does not exist: {input_path}")

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
    candidates = recorder.run(
        "generate_candidates",
        lambda: _generate_candidates(
            propositions,
            config,
            _embedder or _embed_texts,
        ),
    )
    deterministic_edges = recorder.run(
        "derive_deterministic_relations",
        lambda: _derive_deterministic_edges(candidates, propositions),
    )
    llm_edges, llm_reviews = recorder.run(
        "classify_pairs",
        lambda: _classify_candidates(
            candidates,
            propositions,
            config,
            cache,
            state,
            _client,
        ),
    )
    logic_edges, consistency_reviews = recorder.run(
        "validate_consistency",
        lambda: _validate_logic_edges(deterministic_edges + llm_edges),
    )
    review_rows = _dedupe_reviews(parse_reviews + llm_reviews + consistency_reviews)

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
                candidates,
                logic_edges,
                review_rows,
                source_format=source_format,
                input_rows=input_rows,
                input_selection=input_selection,
            ),
        )
        recorder.run("publish_files", lambda: _publish_staged(staging, out_dir))
        input_hash, artifact_hashes = recorder.run(
            "hash_artifacts",
            lambda: (
                _sha256(input_path),
                {
                    name: _sha256(out_dir / name)
                    for name in DISCOVERY_PARQUET_ARTIFACTS
                },
            ),
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
        )
        _write_manifest_last(out_dir, manifest)
        return dict(manifest["stats"])
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
                state.add_usage(usage)
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
            propositions.append(proposition)
            if error or parsed is None:
                reviews.append(
                    _review_row(
                        "parse_error",
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
    )


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
        proposition.get("jurisdiction"),
        proposition.get("outcome"),
        proposition.get("question"),
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
                state.add_usage(usage)
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
                "explanation": classification.explanation,
                "assumptions": classification.assumptions,
                "requires_review": classification.requires_review,
                "discovery_method": "llm",
                "model_version": observed_model,
                "prompt_version": CLASSIFY_PROMPT_VERSION,
            }
        )
        accepted_label = classification.relation not in {"unrelated", "uncertain"}
        accepted = (
            accepted_label
            and classification.confidence >= config.accept_confidence
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
    }


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


def _write_discovery_artifacts(
    directory: Path,
    markets: Sequence[SourceMarket],
    propositions: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    logic_edges: Sequence[dict[str, Any]],
    reviews_: Sequence[dict[str, Any]],
    *,
    source_format: str,
    input_rows: int,
    input_selection: dict[str, object],
) -> dict[str, object]:
    db = DuckDB(directory / "oddsfox_graph.duckdb")
    try:
        db.execute("SET TimeZone = 'UTC'")
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
        _create_and_fill(
            db,
            "relation_candidates_v",
            CANDIDATE_COLUMNS,
            [_published_candidate(row) for row in candidates],
        )
        _create_and_fill(db, "logic_edges_v", LOGIC_EDGE_COLUMNS, logic_edges)
        _create_and_fill(db, "review_queue_v", REVIEW_COLUMNS, reviews_)

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
            "review_queue_v",
            directory / "review_queue.parquet",
            list(REVIEW_COLUMNS),
            "review_id",
        )
        write_conditionals(db, directory)
        write_graph_snapshot(db, directory)
        stats: dict[str, object] = {
            "input_rows": input_rows,
            "input_format": source_format,
            "input_selection": input_selection,
            "markets": len(markets),
            "tokens": len(propositions),
            "active_markets": sum(1 for market in markets if market.is_active),
            "closed_markets": sum(1 for market in markets if market.is_closed),
            "candidate_edges": len(candidates),
            "classified_pairs": sum(
                1 for row in candidates if row.get("discovery_method") == "llm"
            ),
            "unclassified_budget_pairs": sum(
                1 for row in candidates if row.get("status") == "not_classified_budget"
            ),
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
            "parse_failures": sum(
                1 for row in propositions if row["parse_status"] != "parsed"
            ),
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


def _copy_table(
    db: DuckDB,
    table: str,
    path: Path,
    columns: Sequence[str],
    order_by: str,
) -> None:
    projection = ", ".join(columns)
    db.execute(
        f"""
        COPY (
            SELECT {projection}
            FROM {table}
            ORDER BY {order_by}
        ) TO '{q(path)}' (FORMAT PARQUET)
        """
    )


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


def _published_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {name: candidate.get(name) for name in CANDIDATE_COLUMNS}


def _validate_discovery_artifacts(db: DuckDB, directory: Path) -> None:
    contracts = {
        "nodes.parquet": NODE_COLUMNS,
        "market_groups.parquet": MARKET_GROUP_COLUMNS,
        "propositions.parquet": PROPOSITION_COLUMNS,
        "relation_candidates.parquet": CANDIDATE_COLUMNS,
        "logic_edges.parquet": LOGIC_EDGE_COLUMNS,
        "conditional_edges.parquet": {
            name: ""
            for name in ARTIFACT_COLUMNS["conditional_edges.parquet"]
        },
        "review_queue.parquet": REVIEW_COLUMNS,
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
) -> dict[str, object]:
    artifact_names = [
        *DISCOVERY_PARQUET_ARTIFACTS,
        GRAPH_SNAPSHOT_ARTIFACT,
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
        "limits": {
            "accept_confidence": config.accept_confidence,
            "parse_confidence": config.parse_confidence,
            "top_k": config.top_k,
            "max_propositions": config.max_propositions,
            "max_candidates": config.max_candidates,
            "max_llm_pairs": config.max_llm_pairs,
            "llm_concurrency": config.llm_concurrency,
        },
        "cache": {
            "directory": str(cache.directory),
            "offline": config.offline,
            **cache.stats(),
        },
        "usage": state.usage_manifest(),
        "artifacts": artifact_names,
        "artifact_hashes": artifact_hashes,
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
    os.replace(staging / GRAPH_SNAPSHOT_ARTIFACT, out_dir / GRAPH_SNAPSHOT_ARTIFACT)
    reports_out = out_dir / "reports"
    reports_out.mkdir(exist_ok=True)
    for name in REPORTS:
        os.replace(staging / "reports" / name, reports_out / name)


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
