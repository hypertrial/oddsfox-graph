from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as exc:  # pragma: no cover - exercised by CLI installation error
    raise ImportError(
        'Automated discovery requires `pip install -e ".[discovery]"`.'
    ) from exc

from ._diagnostic_stages import write_conditionals
from . import __version__
from .artifacts import ARTIFACT_COLUMNS, REPORTS, reports
from .graph_snapshot import GRAPH_SNAPSHOT_ARTIFACT, write_graph_snapshot
from .queries import DuckDB, q
from .reports import write_reports


PARSE_PROMPT_VERSION = "proposition-parse-v1"
CLASSIFY_PROMPT_VERSION = "relation-classify-v1"
RULE_VERSION = "discovery-rules-v1"
DEFAULT_EMBEDDING_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"

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


class ParsedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    subject: list[str] = Field(min_length=1)
    predicate: str | None
    object: str | None
    operator: (
        Literal[
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "equal",
        ]
        | None
    )
    threshold: float | None
    unit: str | None
    time_start: datetime | None
    time_end: datetime | None
    competition: str | None
    jurisdiction: str | None
    polarity: Literal["positive", "negative"]
    parse_confidence: float = Field(ge=0.0, le=1.0)


class ParsedMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    propositions: list[ParsedOutcome] = Field(min_length=1)


class ParsedMarketBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markets: list[ParsedMarket]


class PairClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    relation: Literal[
        "equivalent",
        "A_implies_B",
        "B_implies_A",
        "mutually_exclusive",
        "complement",
        "compatible",
        "unrelated",
        "uncertain",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)
    assumptions: list[str]
    requires_review: bool


class PairClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[PairClassification]


class PropositionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition_id: str
    market_id: str
    event_id: str | None
    event_slug: str | None
    clob_token_id: str
    outcome_index: int
    outcome: str
    question: str
    category: str | None
    tags: list[str]
    subject_original: list[str]
    subject: list[str]
    predicate: str | None
    object_original: str | None
    object: str | None
    operator: (
        Literal[
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "equal",
        ]
        | None
    )
    threshold: float | None
    unit_original: str | None
    unit: str | None
    time_start: datetime | None
    time_end: datetime | None
    competition_original: str | None
    competition: str | None
    jurisdiction_original: str | None
    jurisdiction: str | None
    polarity: Literal["positive", "negative"]
    parse_confidence: float = Field(ge=0.0, le=1.0)
    parse_status: Literal["parsed", "failed"]
    parser_model: str
    prompt_version: str
    source_format: str


@dataclass(frozen=True)
class SourceOutcome:
    outcome_index: int
    outcome: str
    clob_token_id: str


@dataclass(frozen=True)
class SourceMarket:
    market_id: str
    question: str
    outcomes: tuple[SourceOutcome, ...]
    event_id: str | None = None
    event_slug: str | None = None
    category: str | None = None
    tags: tuple[str, ...] = ()
    time_start: datetime | None = None
    time_end: datetime | None = None
    is_active: bool = True
    is_closed: bool = False
    first_seen_ts: datetime | None = None
    last_seen_ts: datetime | None = None
    volume: float | None = None


@dataclass(frozen=True)
class DiscoveryConfig:
    cache_dir: Path | None = None
    offline: bool = False
    parse_model: str = "gpt-5.6-terra"
    classify_model: str = "gpt-5.6-terra"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    accept_confidence: float = 0.95
    parse_confidence: float = 0.95
    top_k: int = 20
    max_propositions: int = 2_000
    max_candidates: int = 40_000
    max_llm_pairs: int = 5_000
    llm_concurrency: int = 8

    def validate(self) -> None:
        if not 0.0 <= self.accept_confidence <= 1.0:
            raise ValueError("accept_confidence must be between 0 and 1")
        if not 0.0 <= self.parse_confidence <= 1.0:
            raise ValueError("parse_confidence must be between 0 and 1")
        for name in (
            "top_k",
            "max_propositions",
            "max_candidates",
            "max_llm_pairs",
            "llm_concurrency",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")


@dataclass
class RunState:
    observed_parse_models: set[str] = field(default_factory=set)
    observed_classify_models: set[str] = field(default_factory=set)
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    )
    def add_usage(self, usage: dict[str, int]) -> None:
        for key in self.usage:
            self.usage[key] += int(usage.get(key, 0))


class JsonCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.writes = 0

    @staticmethod
    def key(
        task: str,
        model: str,
        prompt_version: str,
        prompt_hash: str,
        schema_hash: str,
        payload: object,
    ) -> str:
        raw = json.dumps(
            {
                "task": task,
                "model": model,
                "reasoning_effort": "medium",
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "schema_hash": schema_hash,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            self.misses += 1
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid discovery cache entry {path}: {exc}") from exc
        self.hits += 1
        return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self.directory / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        self.writes += 1


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

_STAGE_RANKS = {
    "round of 32": 0,
    "round of 16": 1,
    "quarterfinal": 2,
    "quarterfinals": 2,
    "semi-final": 3,
    "semi-finals": 3,
    "semifinal": 3,
    "semifinals": 3,
    "final": 4,
    "winner": 5,
    "win": 5,
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
    started = time.time()
    timings: dict[str, float] = {}

    def stage(name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.time()
        value = fn()
        timings[name] = round(time.time() - t0, 3)
        return value

    source_format, input_rows, markets, input_selection = stage(
        "normalize_input",
        lambda: _load_source_markets(
            input_path,
            max_propositions=config.max_propositions,
        ),
    )
    cache_dir = (config.cache_dir or Path(str(out_dir) + ".cache")).resolve()
    cache = JsonCache(cache_dir)
    state = RunState()
    propositions, parse_reviews = stage(
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
    candidates = stage(
        "generate_candidates",
        lambda: _generate_candidates(
            propositions,
            config,
            _embedder or _embed_texts,
        ),
    )
    deterministic_edges = stage(
        "derive_deterministic_relations",
        lambda: _derive_deterministic_edges(candidates, propositions),
    )
    llm_edges, llm_reviews = stage(
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
    logic_edges, consistency_reviews = stage(
        "validate_consistency",
        lambda: _validate_logic_edges(deterministic_edges + llm_edges),
    )
    review_rows = _dedupe_reviews(parse_reviews + llm_reviews + consistency_reviews)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.discovery-", dir=out_dir.parent)
    )
    try:
        stats = stage(
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
                started=started,
            ),
        )
        manifest = _discovery_manifest(
            input_path,
            source_format,
            stats,
            config,
            cache,
            state,
            timings,
            staging,
        )
        (staging / "build_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        stage("publish_completed_build", lambda: _publish_staged(staging, out_dir))
        stats["runtime_seconds"] = round(time.time() - started, 3)
        return stats
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _load_source_markets(
    input_path: Path,
    *,
    max_propositions: int | None = None,
) -> tuple[str, int, list[SourceMarket], dict[str, object]]:
    db = DuckDB()
    try:
        db.execute("SET TimeZone = 'UTC'")
        columns = {
            str(row["name"]).lower()
            for row in db.rows(
                f"SELECT name FROM parquet_schema('{q(input_path)}') "
                "WHERE name != 'duckdb_schema'"
            )
        }
        input_rows = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(input_path)}')") or 0
        )
        if {"market_id", "question", "outcomes", "clob_token_ids"} <= columns:
            invalid_market_rows = int(
                db.scalar(
                    f"""
                    SELECT count(*)
                    FROM read_parquet('{q(input_path)}')
                    WHERE market_id IS NULL
                       OR question IS NULL
                       OR outcomes IS NULL
                       OR clob_token_ids IS NULL
                       OR len(outcomes) = 0
                       OR len(outcomes) != len(clob_token_ids)
                    """
                )
                or 0
            )
            input_propositions = int(
                db.scalar(
                    f"""
                    SELECT sum(coalesce(len(outcomes), 0))
                    FROM read_parquet('{q(input_path)}')
                    """
                )
                or 0
            )
            markets = _load_compact_markets(
                db,
                input_path,
                columns,
                skip_invalid=max_propositions is not None,
            )
            source_format = "market_snapshot"
        elif {
            "market_id",
            "question",
            "outcome_label",
            "clob_token_id",
            "event_slug",
        } <= columns and (
            "odds_timestamp_epoch" in columns or "odds_hour_epoch" in columns
        ):
            markets = _load_odds_markets(db, input_path, columns)
            invalid_market_rows = 0
            input_propositions = sum(len(market.outcomes) for market in markets)
            source_format = (
                "minutely"
                if "odds_timestamp_epoch" in columns
                else "hourly"
            )
        else:
            raise ValueError(
                "Discovery input must be a compact market snapshot or a supported "
                "OddsFox minutely/hourly export"
            )
    finally:
        db.close()
    _validate_source_markets(markets)
    eligible_markets = len(markets)
    eligible_propositions = sum(len(market.outcomes) for market in markets)
    if max_propositions is not None:
        markets = _select_source_markets(markets, max_propositions)
    selection = {
        "strategy": (
            "volume_desc_then_market_id"
            if max_propositions is not None
            else "all_eligible_markets"
        ),
        "input_market_rows": input_rows if source_format == "market_snapshot" else None,
        "input_rows": input_rows,
        "input_propositions": input_propositions,
        "invalid_market_rows": invalid_market_rows,
        "eligible_markets": eligible_markets,
        "eligible_propositions": eligible_propositions,
        "selected_markets": len(markets),
        "selected_propositions": sum(len(market.outcomes) for market in markets),
        "truncated": len(markets) < eligible_markets,
    }
    return source_format, input_rows, markets, selection


def _load_compact_markets(
    db: DuckDB,
    input_path: Path,
    columns: set[str],
    *,
    skip_invalid: bool = False,
) -> list[SourceMarket]:
    rows = db.rows(
        f"""
        SELECT
            market_id::VARCHAR AS market_id,
            question::VARCHAR AS question,
            outcomes,
            clob_token_ids,
            {_optional_sql(columns, "event_id", "VARCHAR")},
            {_optional_sql(columns, "event_slug", "VARCHAR")},
            {_optional_sql(columns, "category", "VARCHAR")},
            {_optional_sql(columns, "tags", "VARCHAR[]", "[]::VARCHAR[]")},
            {_optional_sql(columns, "volume", "DOUBLE")},
            {_timestamp_sql(columns, ("time_start", "start_time", "start_date"), "time_start")},
            {_timestamp_sql(columns, ("time_end", "end_time", "end_date"), "time_end")}
        FROM read_parquet('{q(input_path)}')
        ORDER BY market_id
        """
    )
    markets = []
    for row in rows:
        outcomes = list(row["outcomes"] or [])
        tokens = list(row["clob_token_ids"] or [])
        if (
            row["market_id"] is None
            or row["question"] is None
            or len(outcomes) != len(tokens)
            or not outcomes
        ):
            if skip_invalid:
                continue
            raise ValueError(
                f"Market {row['market_id']!r} must have non-empty equal-length "
                "outcomes and clob_token_ids"
            )
        markets.append(
            SourceMarket(
                market_id=str(row["market_id"]),
                question=str(row["question"]),
                event_id=_str_or_none(row.get("event_id")),
                event_slug=_str_or_none(row.get("event_slug")),
                category=_str_or_none(row.get("category")),
                tags=tuple(str(tag) for tag in (row.get("tags") or [])),
                time_start=_datetime_or_none(row.get("time_start")),
                time_end=_datetime_or_none(row.get("time_end")),
                volume=(
                    float(row["volume"])
                    if row.get("volume") is not None
                    else None
                ),
                outcomes=tuple(
                    SourceOutcome(index, str(outcome), str(token))
                    for index, (outcome, token) in enumerate(
                        zip(outcomes, tokens, strict=True)
                    )
                ),
            )
        )
    return markets


def _select_source_markets(
    markets: Sequence[SourceMarket],
    max_propositions: int,
) -> list[SourceMarket]:
    selected: list[SourceMarket] = []
    selected_propositions = 0
    ordered = sorted(
        markets,
        key=lambda market: (
            -(market.volume if market.volume is not None else float("-inf")),
            market.market_id,
        ),
    )
    for market in ordered:
        next_count = selected_propositions + len(market.outcomes)
        if next_count > max_propositions:
            continue
        selected.append(market)
        selected_propositions = next_count
        if selected_propositions == max_propositions:
            break
    if not selected:
        raise ValueError(
            "No complete market fits within max_propositions="
            f"{max_propositions}"
        )
    return sorted(selected, key=lambda market: market.market_id)


def _load_odds_markets(
    db: DuckDB, input_path: Path, columns: set[str]
) -> list[SourceMarket]:
    epoch = (
        "odds_timestamp_epoch"
        if "odds_timestamp_epoch" in columns
        else "odds_hour_epoch"
    )
    timestamp = (
        "odds_timestamp" if "odds_timestamp" in columns else "odds_hour_utc"
    )
    rows = db.rows(
        f"""
        WITH ranked AS (
            SELECT
                *,
                min({timestamp}) OVER (PARTITION BY clob_token_id) AS first_seen_ts,
                max({timestamp}) OVER (PARTITION BY clob_token_id) AS last_seen_ts,
                row_number() OVER (
                    PARTITION BY clob_token_id
                    ORDER BY {epoch} DESC
                ) AS rn
            FROM read_parquet('{q(input_path)}')
        )
        SELECT
            market_id::VARCHAR AS market_id,
            outcome_index::INTEGER AS outcome_index,
            clob_token_id::VARCHAR AS clob_token_id,
            question::VARCHAR AS question,
            outcome_label::VARCHAR AS outcome,
            event_slug::VARCHAR AS event_slug,
            {_optional_sql(columns, "event_id", "VARCHAR")},
            {_optional_sql(columns, "category", "VARCHAR")},
            {_optional_sql(columns, "tags", "VARCHAR[]", "[]::VARCHAR[]")},
            {_optional_sql(columns, "market_volume_usd", "DOUBLE")},
            is_active::BOOLEAN AS is_active,
            is_closed::BOOLEAN AS is_closed,
            first_seen_ts,
            last_seen_ts
        FROM ranked
        WHERE rn = 1
        ORDER BY market_id, outcome_index, clob_token_id
        """
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["market_id"])].append(row)
    markets = []
    for market_id in sorted(grouped):
        items = grouped[market_id]
        first = items[0]
        markets.append(
            SourceMarket(
                market_id=market_id,
                question=str(first["question"]),
                event_id=_str_or_none(first.get("event_id")),
                event_slug=_str_or_none(first.get("event_slug")),
                category=_str_or_none(first.get("category")),
                tags=tuple(str(tag) for tag in (first.get("tags") or [])),
                is_active=any(bool(item["is_active"]) for item in items),
                is_closed=any(bool(item["is_closed"]) for item in items),
                first_seen_ts=min(
                    (
                        item["first_seen_ts"]
                        for item in items
                        if item["first_seen_ts"] is not None
                    ),
                    default=None,
                ),
                last_seen_ts=max(
                    (
                        item["last_seen_ts"]
                        for item in items
                        if item["last_seen_ts"] is not None
                    ),
                    default=None,
                ),
                volume=max(
                    (
                        float(item["market_volume_usd"])
                        for item in items
                        if item.get("market_volume_usd") is not None
                    ),
                    default=None,
                ),
                outcomes=tuple(
                    SourceOutcome(
                        int(item["outcome_index"]),
                        str(item["outcome"]),
                        str(item["clob_token_id"]),
                    )
                    for item in items
                ),
            )
        )
    return markets


def _optional_sql(
    columns: set[str],
    name: str,
    sql_type: str,
    fallback: str | None = None,
) -> str:
    if name in columns:
        return f"{name} AS {name}"
    return f"{fallback or f'NULL::{sql_type}'} AS {name}"


def _timestamp_sql(
    columns: set[str], candidates: Sequence[str], alias: str
) -> str:
    for name in candidates:
        if name in columns:
            return f"try_cast({name} AS TIMESTAMPTZ) AS {alias}"
    return f"NULL::TIMESTAMPTZ AS {alias}"


def _validate_source_markets(markets: Sequence[SourceMarket]) -> None:
    if not markets:
        raise ValueError("Discovery input contains no markets")
    market_ids: set[str] = set()
    proposition_ids: set[str] = set()
    for market in markets:
        if not market.market_id or not market.question:
            raise ValueError("market_id and question must be non-empty")
        if market.market_id in market_ids:
            raise ValueError(f"Duplicate market_id {market.market_id!r}")
        market_ids.add(market.market_id)
        outcomes = [outcome.outcome for outcome in market.outcomes]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError(f"Market {market.market_id!r} has duplicate outcomes")
        for outcome in market.outcomes:
            if not outcome.clob_token_id:
                raise ValueError(
                    f"Market {market.market_id!r} has an empty clob_token_id"
                )
            if outcome.clob_token_id in proposition_ids:
                raise ValueError(
                    f"clob_token_id {outcome.clob_token_id!r} belongs to multiple outcomes"
                )
            proposition_ids.add(outcome.clob_token_id)


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
        entry = cache.get(key)
        if entry is None:
            missing.append((market, key, payload))
        else:
            cached[market.market_id] = entry

    if missing:
        if config.offline:
            raise ValueError(
                f"Offline discovery cache is missing {len(missing)} proposition parse entries"
            )
        client = client or _new_openai_client()
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
            if isinstance(result, Exception):
                error = str(result)
            else:
                parsed, observed_model, usage = result
                by_id = {
                    market.market_id: market.model_dump(mode="json")
                    for market in parsed.markets
                }
                state.observed_parse_models.add(observed_model)
                state.add_usage(usage)
            for payload in batch_items:
                market_id = str(payload["market_id"])
                source_market, key, _ = next(
                    item for item in missing if item[0].market_id == market_id
                )
                market_error = error
                parsed_market = by_id.get(market_id)
                if parsed_market is None and market_error is None:
                    market_error = "structured output omitted this market"
                entry = {
                    "parsed": parsed_market,
                    "error": market_error,
                    "observed_model": observed_model,
                    "usage": usage,
                }
                cache.put(key, entry)
                cached[source_market.market_id] = entry

    propositions: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for market in markets:
        entry = cached[market.market_id]
        observed_model = str(entry.get("observed_model") or config.parse_model)
        state.observed_parse_models.add(observed_model)
        parsed_market: ParsedMarket | None = None
        error = _str_or_none(entry.get("error"))
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
    if len(propositions) < 2:
        return []
    proposition_by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    ids = sorted(proposition_by_id)
    index_by_id = {proposition_id: index for index, proposition_id in enumerate(ids)}
    pairs: dict[tuple[str, str], dict[str, Any]] = {}

    def add_pair(
        first: str,
        second: str,
        reason: str,
        *,
        similarity: float | None = None,
        rank: int | None = None,
    ) -> None:
        if first == second:
            return
        a_id, b_id = sorted((first, second))
        row = pairs.setdefault(
            (a_id, b_id),
            {
                "proposition_a_id": a_id,
                "proposition_b_id": b_id,
                "candidate_reasons": set(),
                "embedding_similarity": None,
                "embedding_rank": None,
                "deterministic_relation": None,
                "classification_relation": None,
                "classification_confidence": None,
                "explanation": None,
                "assumptions": [],
                "requires_review": False,
                "status": "pending",
                "discovery_method": None,
                "model_version": None,
                "prompt_version": None,
            },
        )
        row["candidate_reasons"].add(reason)
        if similarity is not None:
            current = row["embedding_similarity"]
            row["embedding_similarity"] = (
                similarity if current is None else max(float(current), similarity)
            )
        if rank is not None:
            current_rank = row["embedding_rank"]
            row["embedding_rank"] = (
                rank if current_rank is None else min(int(current_rank), rank)
            )

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for proposition_id in ids:
        proposition = proposition_by_id[proposition_id]
        grouped[("market", str(proposition["market_id"]))].append(proposition_id)
        event_key = proposition.get("event_id") or proposition.get("event_slug")
        if event_key:
            grouped[("event", str(event_key))].append(proposition_id)
        if proposition.get("competition"):
            grouped[("competition", str(proposition["competition"]))].append(
                proposition_id
            )
        for subject in proposition.get("subject") or []:
            grouped[("entity", str(subject))].append(proposition_id)
        if proposition.get("parse_status") == "parsed":
            grouped[
                ("signature", repr(_proposition_signature(proposition)))
            ].append(proposition_id)
            if proposition.get("threshold") is not None:
                grouped[
                    (
                        "numeric_rule",
                        repr(
                            tuple(
                                _hashable(proposition.get(key))
                                for key in _SEMANTIC_KEYS
                                if key != "threshold"
                            )
                        ),
                    )
                ].append(proposition_id)
            if proposition.get("time_start") and proposition.get("time_end"):
                grouped[
                    (
                        "time_rule",
                        repr(
                            tuple(
                                _hashable(proposition.get(key))
                                for key in _SEMANTIC_KEYS
                                if key not in {"time_start", "time_end"}
                            )
                        ),
                    )
                ].append(proposition_id)
    for (kind, _), group_ids in sorted(grouped.items()):
        for first, second in combinations(sorted(set(group_ids)), 2):
            add_pair(first, second, f"shared_{kind}")

    texts = [_embedding_text(proposition_by_id[proposition_id]) for proposition_id in ids]
    embeddings = embedder(texts, config)
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise ImportError(
            'Automated discovery requires `pip install -e ".[discovery]"`.'
        ) from exc
    matrix = np.asarray(embeddings, dtype=np.float32)
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(ids)
        or matrix.shape[1] == 0
        or not np.isfinite(matrix).all()
    ):
        raise ValueError("Embedding model returned an invalid matrix shape")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    similarities = matrix @ matrix.T
    for proposition_id in ids:
        index = index_by_id[proposition_id]
        scores = similarities[index].copy()
        scores[index] = -np.inf
        ranked = np.argsort(-scores, kind="stable")[: min(config.top_k, len(ids) - 1)]
        for rank, other_index in enumerate(ranked, start=1):
            add_pair(
                proposition_id,
                ids[int(other_index)],
                "embedding_top_k",
                similarity=float(scores[int(other_index)]),
                rank=rank,
            )

    for row in pairs.values():
        a = proposition_by_id[str(row["proposition_a_id"])]
        b = proposition_by_id[str(row["proposition_b_id"])]
        reasons: set[str] = row["candidate_reasons"]
        if a.get("predicate") and a.get("predicate") == b.get("predicate"):
            reasons.add("compatible_predicate")
        if a.get("unit") and a.get("unit") == b.get("unit"):
            reasons.add("compatible_unit")
        if _times_overlap(a, b):
            reasons.add("overlapping_dates")
        relation = _deterministic_relation(a, b, config.parse_confidence)
        if relation:
            row["_deterministic"] = relation
            row["deterministic_relation"] = str(relation["edge_type"])
            row["status"] = "accepted"
            row["discovery_method"] = "deterministic"
            row["explanation"] = relation["explanation"]
            row["assumptions"] = []
            row["prompt_version"] = None
            row["model_version"] = None

    deterministic = sorted(
        (row for row in pairs.values() if row.get("_deterministic")),
        key=_candidate_sort_key,
    )
    if len(deterministic) > config.max_candidates:
        raise ValueError(
            f"Deterministic rules produced {len(deterministic)} candidates, exceeding "
            f"max_candidates={config.max_candidates}; refusing to truncate proven relations"
        )
    unresolved = sorted(
        (row for row in pairs.values() if not row.get("_deterministic")),
        key=_candidate_sort_key,
    )
    kept = deterministic + unresolved[: config.max_candidates - len(deterministic)]
    for row in kept:
        row["candidate_reasons"] = sorted(row["candidate_reasons"])
    return sorted(
        kept,
        key=lambda row: (
            str(row["proposition_a_id"]),
            str(row["proposition_b_id"]),
        ),
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


def _candidate_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
    similarity = row["embedding_similarity"]
    return (
        -len(row["candidate_reasons"]),
        -float(similarity if similarity is not None else -1.0),
        str(row["proposition_a_id"]),
        str(row["proposition_b_id"]),
    )


def _deterministic_relation(
    a: dict[str, Any],
    b: dict[str, Any],
    parse_confidence: float,
) -> dict[str, Any] | None:
    a_id = str(a["proposition_id"])
    b_id = str(b["proposition_id"])
    same_market = a["market_id"] == b["market_id"]
    expected_tokens = int(a["_expected_tokens"])
    outcomes = {str(a["outcome"]).casefold(), str(b["outcome"]).casefold()}
    if same_market and expected_tokens == 2 and outcomes == {"yes", "no"}:
        return _rule(
            "complement",
            min(a_id, b_id),
            max(a_id, b_id),
            "same_market",
            "Yes and No outcomes of one binary market are complements",
            1.0,
        )
    if same_market:
        return _rule(
            "mutually_exclusive",
            min(a_id, b_id),
            max(a_id, b_id),
            "same_market",
            "Distinct outcomes of one categorical market cannot both occur",
            1.0,
        )

    if min(float(a["parse_confidence"]), float(b["parse_confidence"])) < parse_confidence:
        return None
    confidence = min(float(a["parse_confidence"]), float(b["parse_confidence"]))

    if _proposition_signature(a) == _proposition_signature(b):
        return _rule(
            "equivalent",
            min(a_id, b_id),
            max(a_id, b_id),
            "normalized_equivalence",
            "Normalized proposition fields are identical",
            confidence,
        )

    threshold_relation = _numeric_threshold_relation(a, b)
    if threshold_relation:
        src, dst = threshold_relation
        return _rule(
            "implies",
            str(src["proposition_id"]),
            str(dst["proposition_id"]),
            "numeric_threshold",
            "A stronger numeric threshold implies the compatible weaker threshold",
            confidence,
        )

    time_relation = _time_window_relation(a, b)
    if time_relation:
        src, dst = time_relation
        return _rule(
            "implies",
            str(src["proposition_id"]),
            str(dst["proposition_id"]),
            "time_window_containment",
            "A narrower compatible time window implies the containing window",
            confidence,
        )

    a_stage = _stage_rank(a)
    b_stage = _stage_rank(b)
    if (
        a_stage is not None
        and b_stage is not None
        and a_stage != b_stage
        and _same_values(a, b, ("subject", "competition", "polarity"))
        and a.get("polarity") == "positive"
    ):
        src, dst = (a, b) if a_stage > b_stage else (b, a)
        return _rule(
            "implies",
            str(src["proposition_id"]),
            str(dst["proposition_id"]),
            "tournament_stage",
            "Reaching a later tournament stage implies reaching an earlier stage",
            confidence,
        )

    if (
        _same_event(a, b)
        and _is_winner_proposition(a)
        and _is_winner_proposition(b)
        and set(a.get("subject") or []) != set(b.get("subject") or [])
        and a.get("polarity") == b.get("polarity") == "positive"
    ):
        return _rule(
            "mutually_exclusive",
            min(a_id, b_id),
            max(a_id, b_id),
            "single_winner",
            "Distinct winners of one single-winner event cannot both occur",
            confidence,
        )
    return None


def _rule(
    edge_type: str,
    src: str,
    dst: str,
    basis: str,
    explanation: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "edge_type": edge_type,
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_basis": basis,
        "explanation": explanation,
        "confidence": confidence,
    }


def _numeric_threshold_relation(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if a.get("threshold") is None or b.get("threshold") is None:
        return None
    if a.get("operator") != b.get("operator"):
        return None
    if not _same_except(a, b, {"threshold", "proposition_id", "market_id", "event_slug", "event_id"}):
        return None
    a_threshold = float(a["threshold"])
    b_threshold = float(b["threshold"])
    if a_threshold == b_threshold:
        return None
    if a["operator"] in {"greater_than", "greater_than_or_equal"}:
        relation = (a, b) if a_threshold > b_threshold else (b, a)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    if a["operator"] in {"less_than", "less_than_or_equal"}:
        relation = (a, b) if a_threshold < b_threshold else (b, a)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    return None


def _time_window_relation(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not all((a.get("time_start"), a.get("time_end"), b.get("time_start"), b.get("time_end"))):
        return None
    if not _same_except(
        a,
        b,
        {
            "time_start",
            "time_end",
            "proposition_id",
            "market_id",
            "event_slug",
            "event_id",
        },
    ):
        return None
    a_contains_b = a["time_start"] <= b["time_start"] and a["time_end"] >= b["time_end"]
    b_contains_a = b["time_start"] <= a["time_start"] and b["time_end"] >= a["time_end"]
    if a_contains_b and not b_contains_a:
        relation = (b, a)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    if b_contains_a and not a_contains_b:
        relation = (a, b)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    return None


_SEMANTIC_KEYS = (
    "subject",
    "predicate",
    "object",
    "operator",
    "threshold",
    "unit",
    "time_start",
    "time_end",
    "competition",
    "jurisdiction",
    "polarity",
)


def _proposition_signature(proposition: dict[str, Any]) -> tuple[object, ...]:
    return tuple(_hashable(proposition.get(key)) for key in _SEMANTIC_KEYS)


def _same_except(
    a: dict[str, Any], b: dict[str, Any], excluded: set[str]
) -> bool:
    return all(
        _hashable(a.get(key)) == _hashable(b.get(key))
        for key in _SEMANTIC_KEYS
        if key not in excluded
    )


def _same_values(
    a: dict[str, Any], b: dict[str, Any], keys: Sequence[str]
) -> bool:
    return all(_hashable(a.get(key)) == _hashable(b.get(key)) for key in keys)


def _stage_rank(proposition: dict[str, Any]) -> int | None:
    values = [proposition.get("object"), proposition.get("predicate")]
    for value in values:
        if not value:
            continue
        normalized = _normalize_text(str(value)).casefold()
        if normalized in _STAGE_RANKS:
            return _STAGE_RANKS[normalized]
        for name, rank in _STAGE_RANKS.items():
            if name in normalized:
                return rank
    return None


def _is_winner_proposition(proposition: dict[str, Any]) -> bool:
    predicate = _normalize_text(str(proposition.get("predicate") or "")).casefold()
    object_ = _normalize_text(str(proposition.get("object") or "")).casefold()
    winner_words = {"win", "winner", "winners", "winning", "wins"}
    return bool(set(predicate.replace("-", " ").split()) & winner_words) or (
        object_ in winner_words
    )


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_event = a.get("event_id") or a.get("event_slug")
    b_event = b.get("event_id") or b.get("event_slug")
    return bool(a_event and a_event == b_event)


def _times_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_start, a_end = a.get("time_start"), a.get("time_end")
    b_start, b_end = b.get("time_start"), b.get("time_end")
    return bool(a_start and a_end and b_start and b_end and a_start <= b_end and b_start <= a_end)


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
        entry = cache.get(key)
        pair_id = str(payload["pair_id"])
        if entry is None:
            missing.append((candidate, key, payload))
        else:
            cached[pair_id] = entry

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
            if isinstance(result, Exception):
                error = str(result)
            else:
                parsed, observed_model, usage = result
                by_pair = {
                    pair.pair_id: pair.model_dump(mode="json")
                    for pair in parsed.pairs
                }
                state.observed_classify_models.add(observed_model)
                state.add_usage(usage)
            for payload in batch_items:
                pair_id = str(payload["pair_id"])
                _, key, _ = missing_by_pair[pair_id]
                pair_error = error
                parsed_pair = by_pair.get(pair_id)
                if parsed_pair is None and pair_error is None:
                    pair_error = "structured output omitted this pair"
                entry = {
                    "parsed": parsed_pair,
                    "error": pair_error,
                    "observed_model": observed_model,
                    "usage": usage,
                }
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
        error = _str_or_none(entry.get("error"))
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
    started: float,
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
            "runtime_seconds": round(time.time() - started, 3),
        }
        write_reports(db, directory, stats)
        _validate_discovery_artifacts(db, directory)
        return stats
    finally:
        db.close()


def _create_and_fill(
    db: DuckDB,
    table: str,
    columns: dict[str, str],
    rows: Sequence[dict[str, Any]],
) -> None:
    ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in columns.items())
    db.execute(f"CREATE TABLE {table} ({ddl})")
    if not rows:
        return
    names = list(columns)
    placeholders = ", ".join("?" for _ in names)
    db.executemany(
        f"INSERT INTO {table} VALUES ({placeholders})",
        [[row.get(name) for name in names] for row in rows],
    )


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
    source_format: str,
    stats: dict[str, object],
    config: DiscoveryConfig,
    cache: JsonCache,
    state: RunState,
    timings: dict[str, float],
    staging: Path,
) -> dict[str, object]:
    artifact_names = [
        *DISCOVERY_PARQUET_ARTIFACTS,
        GRAPH_SNAPSHOT_ARTIFACT,
    ]
    artifact_hashes = {
        name: _sha256(staging / name)
        for name in DISCOVERY_PARQUET_ARTIFACTS
    }
    return {
        "command": "discover",
        "version": __version__,
        "input": str(input_path),
        "input_hash": _sha256(input_path),
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
            "hits": cache.hits,
            "misses": cache.misses,
            "writes": cache.writes,
        },
        "usage": state.usage,
        "artifacts": artifact_names,
        "artifact_hashes": artifact_hashes,
        "reports": list(reports()),
        "stats": stats,
        "stage_timings": timings,
    }


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
    os.replace(staging / "build_manifest.json", manifest)


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


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


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


def _hashable(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return _utc_datetime(parsed)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
