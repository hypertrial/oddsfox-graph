from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Operator = Literal[
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "equal",
]
CitationField = Literal[
    "question",
    "description",
    "outcome",
    "event_id",
    "event_slug",
    "category",
    "tags",
    "time_start",
    "time_end",
]


DEFAULT_EMBEDDING_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DEFAULT_NLI_MODEL = "tasksource/ModernBERT-base-nli"
DEFAULT_NLI_REVISION = "975123f23a50424f9ca95d5382504d24d9ed9fd2"
DEFAULT_PRIMARY_MODEL = "Qwen/Qwen3-4B-GGUF:Q8_0"
DEFAULT_VERIFIER_MODEL = "ibm-granite/granite-3.3-2b-instruct-GGUF:Q8_0"
DEFAULT_RELATION_THRESHOLDS = {
    "complement": 0.995,
    "equivalent": 0.99,
    "mutually_exclusive": 0.99,
    "implies": 0.98,
    "compatible": 0.98,
}


class SupportingField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition: Literal["A", "B"]
    field: Literal[
        "question",
        "description",
        "outcome",
        "subject",
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
    ]
    value: str = Field(min_length=1)


class ParsedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    subject: list[str]
    predicate: str | None
    object: str | None
    operator: Operator | None
    threshold: float | None
    unit: str | None
    time_start: datetime | None
    time_end: datetime | None
    competition: str | None
    event_scope: str | None
    jurisdiction: str | None
    polarity: Literal["positive", "negative"]
    parse_confidence: float = Field(ge=0.0, le=1.0)
    citations: list[CitationField] = Field(min_length=1)


class ParsedMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    propositions: list[ParsedOutcome] = Field(min_length=1)


AtomicJudgment = Literal["yes", "no", "unknown"]


class AtomicPairAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    a_implies_b: AtomicJudgment
    b_implies_a: AtomicJudgment
    can_both_be_true: AtomicJudgment
    must_one_be_true: AtomicJudgment
    logically_related: AtomicJudgment
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_fields: list[SupportingField]
    assumptions: list[str]
    unsupported_assumption: bool
    requires_review: bool


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
    description: str
    market_source_hash: str
    normalization_version: str
    category: str | None
    tags: list[str]
    subject_original: list[str]
    subject: list[str]
    predicate: str | None
    object_original: str | None
    object: str | None
    operator: Operator | None
    threshold: float | None
    unit_original: str | None
    unit: str | None
    time_start: datetime | None
    time_end: datetime | None
    competition_original: str | None
    competition: str | None
    event_scope_original: str | None
    event_scope: str | None
    jurisdiction_original: str | None
    jurisdiction: str | None
    polarity: Literal["positive", "negative"]
    parse_confidence: float = Field(ge=0.0, le=1.0)
    parse_status: Literal["parsed", "failed", "quarantined"]
    primary_parser_model: str | None
    verifier_parser_model: str | None
    prompt_version: str | None
    primary_parse_fingerprint: str | None
    verifier_parse_fingerprint: str | None
    consensus_fingerprint: str | None
    automation_profile_id: str | None
    source_schema: str
    extractor_id: str | None = None
    extractor_version: str | None = None
    extraction_status: Literal["exact", "ambiguous", "unmatched"] | None = None
    source_spans_json: str | None = None
    proof_scope_key: str | None = None


@dataclass(frozen=True)
class SourceOutcome:
    outcome_index: int
    outcome: str
    clob_token_id: str


@dataclass(frozen=True)
class SourceMarket:
    market_id: str
    question: str
    description: str
    outcomes: tuple[SourceOutcome, ...]
    source_hash: str
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
    mode: Literal["fast", "full"] = "full"
    cache_dir: Path | None = None
    incremental_from: Path | None = None
    compute_profile: Path | None = None
    automation_profile: Path | None = None
    primary_model_manifest: Path | None = None
    verifier_model_manifest: Path | None = None
    offline: bool = False
    primary_base_url: str = "http://127.0.0.1:8080/v1"
    verifier_base_url: str = "http://127.0.0.1:8081/v1"
    allow_remote_inference: bool = False
    primary_model: str = DEFAULT_PRIMARY_MODEL
    verifier_model: str = DEFAULT_VERIFIER_MODEL
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    nli_model: str = DEFAULT_NLI_MODEL
    nli_revision: str = DEFAULT_NLI_REVISION
    sampling_seed: int = 0
    temperature: float = 0.1
    generation_top_p: float = 0.8
    generation_top_k: int = 20
    presence_penalty: float = 1.5
    parse_max_output_tokens: int = 4096
    classify_max_output_tokens: int = 1024
    accept_confidence: float = 0.95
    relation_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_RELATION_THRESHOLDS)
    )
    parse_confidence: float = 0.95
    top_k: int = 20
    embedding_block_size: int = 512
    max_propositions: int | None = None
    max_candidates: int = 400_000
    max_llm_pairs: int = 5_000
    classification_coverage_target: float = 0.0
    max_visible_coverage_gap: float = 1.0
    llm_concurrency: int = 2
    output_format: Literal["table", "json", "jsonl"] = "table"
    progress_format: Literal["auto", "plain", "json", "quiet"] = "auto"
    deadline_seconds: float = 3_600.0

    def validate(self) -> None:
        if self.mode not in {"fast", "full"}:
            raise ValueError("mode must be fast or full")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if not 0.0 <= self.accept_confidence <= 1.0:
            raise ValueError("accept_confidence must be between 0 and 1")
        if not 0.0 <= self.parse_confidence <= 1.0:
            raise ValueError("parse_confidence must be between 0 and 1")
        if not 0.0 <= self.classification_coverage_target <= 1.0:
            raise ValueError(
                "classification_coverage_target must be between 0 and 1"
            )
        if not 0.0 <= self.max_visible_coverage_gap <= 1.0:
            raise ValueError("max_visible_coverage_gap must be between 0 and 1")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if not 0.0 < self.generation_top_p <= 1.0:
            raise ValueError("generation_top_p must be greater than 0 and at most 1")
        if self.generation_top_k < 1:
            raise ValueError("generation_top_k must be positive")
        if not -2.0 <= self.presence_penalty <= 2.0:
            raise ValueError("presence_penalty must be between -2 and 2")
        if self.sampling_seed < 0:
            raise ValueError("sampling_seed must be non-negative")
        allowed_relations = set(DEFAULT_RELATION_THRESHOLDS)
        unknown = set(self.relation_thresholds) - allowed_relations
        if unknown:
            raise ValueError(
                "Unknown relation thresholds: " + ", ".join(sorted(unknown))
            )
        for relation, threshold in self.relation_thresholds.items():
            if not 0.0 <= float(threshold) <= 1.0:
                raise ValueError(
                    f"relation threshold for {relation} must be between 0 and 1"
                )
        for name in (
            "top_k",
            "embedding_block_size",
            "max_candidates",
            "max_llm_pairs",
            "llm_concurrency",
            "parse_max_output_tokens",
            "classify_max_output_tokens",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_propositions is not None and self.max_propositions < 1:
            raise ValueError("max_propositions must be positive when supplied")

    def threshold_for(self, relation: str) -> float:
        normalized = "implies" if relation in {"A_implies_B", "B_implies_A"} else relation
        return float(self.relation_thresholds.get(normalized, self.accept_confidence))
