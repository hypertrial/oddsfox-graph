from __future__ import annotations

from typing import Any

from .contracts import ParsedMarket, ParsedOutcome, SourceMarket, SourceOutcome
from .input import utc_datetime
from .protocol import deterministic_extract
from .relations import normalize_text
from .versions import NORMALIZATION_VERSION, PARSE_PROMPT_VERSION


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


def validate_parsed_market(source: SourceMarket, parsed: ParsedMarket) -> None:
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


def proposition_row(
    market: SourceMarket,
    source: SourceOutcome,
    parsed: ParsedOutcome | None,
    observed_model: str,
    source_schema: str,
    error: str | None,
    inference_fingerprint: str,
    model_profile_id: str | None,
) -> dict[str, Any]:
    original_subject = parsed.subject if parsed else []
    object_original = parsed.object if parsed else None
    unit_original = parsed.unit if parsed else None
    competition_original = parsed.competition if parsed else None
    event_scope_original = parsed.event_scope if parsed else None
    jurisdiction_original = parsed.jurisdiction if parsed else None
    extracted = deterministic_extract(market, source)
    disagreements: list[str] = []
    for field in ("polarity", "operator", "threshold", "unit"):
        authoritative = extracted.get(field)
        model_value = getattr(parsed, field, None) if parsed else None
        if authoritative is not None and model_value is not None:
            normalized_model = (
                canonical_unit(str(model_value))
                if field == "unit"
                else model_value
            )
            if normalized_model != authoritative:
                disagreements.append(
                    f"model {field}={model_value!r} disagrees with "
                    f"authoritative extraction {authoritative!r}"
                )
    polarity = str(
        extracted.get("polarity")
        or (parsed.polarity if parsed else "positive")
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
                canonical_entity(subject)
                for subject in original_subject
                if normalize_text(subject)
            }
        ),
        "predicate": normalize_optional(parsed.predicate if parsed else None),
        "object_original": object_original,
        "object": canonical_entity(object_original) if object_original else None,
        "operator": extracted.get("operator")
        or (parsed.operator if parsed else None),
        "threshold": (
            extracted["threshold"]
            if extracted.get("threshold") is not None
            else (parsed.threshold if parsed else None)
        ),
        "unit_original": unit_original,
        "unit": (
            extracted.get("unit")
            or (canonical_unit(unit_original) if unit_original else None)
        ),
        "time_start": utc_datetime(
            market.time_start or (parsed.time_start if parsed else None)
        ),
        "time_end": utc_datetime(
            market.time_end or (parsed.time_end if parsed else None)
        ),
        "competition_original": competition_original,
        "competition": (
            canonical_entity(competition_original)
            if competition_original
            else None
        ),
        "event_scope_original": event_scope_original,
        "event_scope": (
            canonical_entity(event_scope_original)
            if event_scope_original
            else None
        ),
        "jurisdiction_original": jurisdiction_original,
        "jurisdiction": (
            canonical_entity(jurisdiction_original)
            if jurisdiction_original
            else None
        ),
        "polarity": polarity,
        "parse_confidence": (
            parsed.parse_confidence if parsed and not error else 0.0
        ),
        "parse_status": "parsed" if parsed and not error else "failed",
        "parser_model": observed_model,
        "prompt_version": PARSE_PROMPT_VERSION,
        "inference_fingerprint": inference_fingerprint,
        "model_profile_id": model_profile_id,
        "source_schema": source_schema,
        "_expected_tokens": len(market.outcomes),
        "_is_active": market.is_active,
        "_is_closed": market.is_closed,
        "_first_seen_ts": market.first_seen_ts or market.time_start,
        "_last_seen_ts": market.last_seen_ts or market.time_end,
        "_authoritative_disagreements": disagreements,
    }


def normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    return normalized or None


def canonical_entity(value: str) -> str:
    normalized = normalize_text(value)
    return _ENTITY_ALIASES.get(normalized.casefold(), normalized)


def canonical_unit(value: str) -> str:
    normalized = normalize_text(value)
    return _UNIT_ALIASES.get(normalized.casefold(), normalized)
