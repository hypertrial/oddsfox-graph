from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DEFAULT_EMBEDDING_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


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
