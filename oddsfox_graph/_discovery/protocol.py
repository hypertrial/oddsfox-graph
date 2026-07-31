from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
    PropositionRecord,
    SourceMarket,
    SourceOutcome,
)
from .inference import GenerationSettings
from .provenance import text_sha256
from .versions import (
    CLASSIFY_PROMPT_VERSION,
    NORMALIZATION_VERSION,
    PARSE_PROMPT_VERSION,
    SOURCE_SCHEMA,
)


PARSE_PROMPT = """/no_think
Extract one proposition for every supplied market outcome.
Use the question, full description, outcome, and authoritative metadata only.
Use the outcome string exactly as supplied. Normalize dates, numbers, and units.
Use null for information that is absent or not supported by the schema; never invent it.
For Yes/No markets, set No to negative polarity.
Field semantics: subject is the entity set; predicate is the event; object is its
target; operator/threshold/unit describe comparisons; time_start/time_end are UTC
resolution bounds; competition, event_scope, and jurisdiction constrain identity;
polarity says whether the outcome asserts or negates the proposition; confidence is
your confidence that every field is supported. Return this market and every supplied
outcome exactly once."""

CLASSIFY_PROMPT = """/no_think
Judge one proposition pair using only the supplied fields. Answer yes, no, or unknown
for: A entails B; B entails A; both can be true; at least one must be true; and the
propositions are logically related. Confidence is joint confidence in all judgments.
Every cited supporting field must name A or B, a supplied nonempty field, and its exact
supplied value. List every assumption. Set unsupported_assumption=true whenever an
assumption is not established by the supplied evidence. Set requires_review=true for
unknown, contradictory, context-dependent, or unsupported conclusions. Do not infer
from prices or probabilities. Return the supplied pair_id exactly."""


class ParseOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    clob_token_id: str
    authoritative_extraction: dict[str, object]


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    question: str
    description: str
    market_source_hash: str
    event_id: str | None
    event_slug: str | None
    category: str | None
    tags: list[str]
    time_start: datetime | None
    time_end: datetime | None
    outcomes: list[ParseOutcomeRequest]


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    proposition_A: PropositionRecord
    proposition_B: PropositionRecord


_COMPARISON_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:at least|no less than)\s+", "greater_than_or_equal"),
    (r"\b(?:more than|over|above|greater than)\s+", "greater_than"),
    (r"\b(?:at most|no more than)\s+", "less_than_or_equal"),
    (r"\b(?:less than|under|below)\s+", "less_than"),
    (r"\b(?:exactly|equal to)\s+", "equal"),
)
_NUMBER_PATTERN = re.compile(
    r"(?P<currency>\$)?(?P<number>-?\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<percent>%|percent|percentage)?",
    re.IGNORECASE,
)


def generation_settings(
    config: DiscoveryConfig,
    *,
    role: Literal["parse", "classify"],
) -> GenerationSettings:
    return GenerationSettings(
        seed=config.sampling_seed,
        temperature=config.temperature,
        top_p=config.generation_top_p,
        top_k=config.generation_top_k,
        presence_penalty=config.presence_penalty,
        max_output_tokens=(
            config.parse_max_output_tokens
            if role == "parse"
            else config.classify_max_output_tokens
        ),
    )


def default_generation_settings(
    *,
    role: Literal["parse", "classify"],
) -> GenerationSettings:
    return generation_settings(DiscoveryConfig(), role=role)


def deterministic_extract(
    market: SourceMarket,
    outcome: str | SourceOutcome,
) -> dict[str, object]:
    result: dict[str, object] = {}
    outcome_value = outcome.outcome if isinstance(outcome, SourceOutcome) else outcome
    outcome_name = outcome_value.casefold().strip()
    if outcome_name in {"yes", "no"}:
        result["polarity"] = "negative" if outcome_name == "no" else "positive"
    normalized_question = market.question.casefold()
    operator: str | None = None
    match_start = 0
    for pattern, candidate in _COMPARISON_PATTERNS:
        match = re.search(pattern, normalized_question)
        if match:
            operator = candidate
            match_start = match.end()
            break
    if operator is None:
        return result
    number = _NUMBER_PATTERN.search(market.question, pos=match_start)
    if number is None:
        return result
    result["operator"] = operator
    result["threshold"] = float(number.group("number").replace(",", ""))
    if number.group("currency"):
        result["unit"] = "USD"
    elif number.group("percent"):
        result["unit"] = "percent"
    return result


def market_request(market: SourceMarket) -> ParseRequest:
    return ParseRequest(
        market_id=market.market_id,
        question=market.question,
        description=market.description,
        market_source_hash=market.source_hash,
        event_id=market.event_id,
        event_slug=market.event_slug,
        category=market.category,
        tags=list(market.tags),
        time_start=market.time_start,
        time_end=market.time_end,
        outcomes=[
            ParseOutcomeRequest(
                outcome=outcome.outcome,
                clob_token_id=outcome.clob_token_id,
                authoritative_extraction=deterministic_extract(
                    market,
                    outcome.outcome,
                ),
            )
            for outcome in market.outcomes
        ],
    )


def public_proposition(proposition: dict[str, Any]) -> dict[str, object]:
    public = {
        key: value
        for key, value in proposition.items()
        if not key.startswith("_")
    }
    return PropositionRecord.model_validate(public).model_dump(mode="json")


def pair_identifier(a_id: str, b_id: str) -> str:
    left, right = sorted((a_id, b_id))
    return text_sha256(f"{left}|{right}")


def pair_request(
    proposition_a: dict[str, Any],
    proposition_b: dict[str, Any],
) -> PairRequest:
    a_id = str(proposition_a["proposition_id"])
    b_id = str(proposition_b["proposition_id"])
    return PairRequest(
        pair_id=pair_identifier(a_id, b_id),
        proposition_A=PropositionRecord.model_validate(
            public_proposition(proposition_a)
        ),
        proposition_B=PropositionRecord.model_validate(
            public_proposition(proposition_b)
        ),
    )


def conformance_parse_request() -> ParseRequest:
    return ParseRequest(
        market_id="conformance-market",
        question="Will the conformance check pass?",
        description="A local schema-conformance request.",
        market_source_hash=text_sha256("conformance-market"),
        event_id=None,
        event_slug=None,
        category=None,
        tags=[],
        time_start=None,
        time_end=None,
        outcomes=[
            ParseOutcomeRequest(
                outcome="Yes",
                clob_token_id="yes",
                authoritative_extraction={"polarity": "positive"},
            ),
            ParseOutcomeRequest(
                outcome="No",
                clob_token_id="no",
                authoritative_extraction={"polarity": "negative"},
            ),
        ],
    )


def conformance_pair_request(model_id: str) -> PairRequest:
    common: dict[str, object] = {
        "market_id": "conformance-market",
        "event_id": None,
        "event_slug": None,
        "question": "Will the conformance check pass?",
        "description": "A local schema-conformance request.",
        "market_source_hash": text_sha256("conformance-market"),
        "normalization_version": NORMALIZATION_VERSION,
        "category": None,
        "tags": [],
        "subject_original": ["conformance check"],
        "subject": ["conformance check"],
        "predicate": "pass",
        "object_original": None,
        "object": None,
        "operator": None,
        "threshold": None,
        "unit_original": None,
        "unit": None,
        "time_start": None,
        "time_end": None,
        "competition_original": None,
        "competition": None,
        "event_scope_original": None,
        "event_scope": None,
        "jurisdiction_original": None,
        "jurisdiction": None,
        "parse_confidence": 1.0,
        "parse_status": "parsed",
        "parser_model": model_id,
        "prompt_version": PARSE_PROMPT_VERSION,
        "inference_fingerprint": None,
        "model_profile_id": None,
        "source_schema": SOURCE_SCHEMA,
    }
    yes = PropositionRecord.model_validate(
        {
            **common,
            "proposition_id": "yes",
            "clob_token_id": "yes",
            "outcome_index": 0,
            "outcome": "Yes",
            "polarity": "positive",
        }
    )
    no = PropositionRecord.model_validate(
        {
            **common,
            "proposition_id": "no",
            "clob_token_id": "no",
            "outcome_index": 1,
            "outcome": "No",
            "polarity": "negative",
        }
    )
    return PairRequest(
        pair_id="conformance-pair",
        proposition_A=yes,
        proposition_B=no,
    )


def model_schema_hash(model: type[BaseModel]) -> str:
    return text_sha256(
        json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
    )


def parse_request_hash() -> str:
    return model_schema_hash(ParseRequest)


def classify_request_hash() -> str:
    return model_schema_hash(PairRequest)


def protocol_metadata() -> dict[str, dict[str, str]]:
    return {
        "parse": {
            "prompt_version": PARSE_PROMPT_VERSION,
            "prompt_hash": text_sha256(PARSE_PROMPT),
            "request_schema_hash": parse_request_hash(),
            "response_schema_hash": model_schema_hash(ParsedMarket),
        },
        "classify": {
            "prompt_version": CLASSIFY_PROMPT_VERSION,
            "prompt_hash": text_sha256(CLASSIFY_PROMPT),
            "request_schema_hash": classify_request_hash(),
            "response_schema_hash": model_schema_hash(AtomicPairAssessment),
        },
    }
