from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from .contracts import ParsedMarket, ParsedOutcome, SourceMarket, SourceOutcome
from .input import utc_datetime
from .protocol import deterministic_extract
from .extraction import extract_proposition
from .relations import normalize_text
from .versions import EXTRACTOR_ID, EXTRACTOR_VERSION, NORMALIZATION_VERSION, PARSE_PROMPT_VERSION


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


def canonicalize_parsed_market(
    source: SourceMarket,
    parsed: ParsedMarket,
) -> ParsedMarket:
    """Restore authoritative outcome spelling after model normalization."""

    expected = {
        outcome.outcome.casefold().strip(): outcome.outcome
        for outcome in source.outcomes
    }
    if len(expected) != len(source.outcomes):
        raise ValueError(f"Source outcomes for {source.market_id!r} are ambiguous")
    propositions: list[ParsedOutcome] = []
    for outcome in parsed.propositions:
        authoritative = expected.get(outcome.outcome.casefold().strip())
        if authoritative is None:
            raise ValueError(
                f"Structured parse outcome {outcome.outcome!r} does not match input"
            )
        propositions.append(outcome.model_copy(update={"outcome": authoritative}))
    canonical = parsed.model_copy(update={"propositions": propositions})
    validate_parsed_market(source, canonical)
    return canonical


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
    available_citations: set[str] = {"question", "outcome"}
    for field, value in (
        ("description", source.description),
        ("event_id", source.event_id),
        ("event_slug", source.event_slug),
        ("category", source.category),
        ("tags", source.tags),
        ("time_start", source.time_start),
        ("time_end", source.time_end),
    ):
        if value not in (None, "", ()):
            available_citations.add(field)
    for outcome in parsed.propositions:
        invalid = {str(value) for value in outcome.citations} - available_citations
        if invalid:
            raise ValueError(
                "Parse citations reference empty or unavailable source fields: "
                + ", ".join(sorted(invalid))
            )


def proposition_row(
    market: SourceMarket,
    source: SourceOutcome,
    parsed: ParsedOutcome | None,
    primary_model: str | None,
    verifier_model: str | None,
    source_schema: str,
    error: str | None,
    primary_fingerprint: str | None,
    verifier_fingerprint: str | None,
    consensus_fingerprint: str | None,
    automation_profile_id: str | None,
) -> dict[str, Any]:
    strict = extract_proposition(market, source)
    original_subject = parsed.subject if parsed else list(strict.subject)
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
        "predicate": normalize_optional(parsed.predicate if parsed else strict.predicate),
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
        "primary_parser_model": primary_model,
        "verifier_parser_model": verifier_model,
        "prompt_version": PARSE_PROMPT_VERSION if primary_model else None,
        "primary_parse_fingerprint": primary_fingerprint,
        "verifier_parse_fingerprint": verifier_fingerprint,
        "consensus_fingerprint": consensus_fingerprint,
        "automation_profile_id": automation_profile_id,
        "source_schema": source_schema,
        "extractor_id": EXTRACTOR_ID,
        "extractor_version": EXTRACTOR_VERSION,
        "extraction_status": strict.status,
        "source_spans_json": strict.spans_json(),
        "proof_scope_key": strict.proof_scope_key,
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


def select_model_parse_fallback_markets(
    markets: Sequence[SourceMarket],
    *,
    limit: int,
) -> tuple[str, ...]:
    """Select bounded ambiguous parses using only structured candidate value."""

    event_keys = [
        normalize_text(market.event_id or market.event_slug or "")
        for market in markets
    ]
    event_counts = Counter(key for key in event_keys if key)
    ranked: list[tuple[int, int, str]] = []
    for market, event_key in zip(markets, event_keys, strict=True):
        statuses = {
            extract_proposition(market, outcome).status
            for outcome in market.outcomes
        }
        ambiguous = "ambiguous" in statuses
        unmatched = "unmatched" in statuses
        group_size = event_counts.get(event_key, 0)
        if not ambiguous and not (unmatched and group_size > 1):
            continue
        ranked.append((0 if ambiguous else 1, -group_size, market.market_id))
    return tuple(row[2] for row in sorted(ranked)[: max(0, limit)])
