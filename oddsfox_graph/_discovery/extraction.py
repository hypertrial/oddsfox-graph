"""Strict deterministic extraction for the complete-catalog fast path.

The extractor deliberately prefers ``unmatched`` over an inferred value.  Every
accepted value carries literal source spans so deterministic rules can prove
which catalog text they used.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timezone
from functools import lru_cache
from typing import Literal

from .contracts import CitationField, Operator, SourceMarket, SourceOutcome
from .provenance import canonical_json_sha256
from .relations import normalize_text
from .versions import (
    EXTRACTOR_ID,
    EXTRACTOR_VERSION,
    PROOF_SCOPE_VERSION,
    RULE_APPLICABILITY_VERSION,
)


ExtractionStatus = Literal["exact", "ambiguous", "unmatched"]


@dataclass(frozen=True)
class SourceSpan:
    field: CitationField
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ExtractedProposition:
    status: ExtractionStatus
    polarity: Literal["positive", "negative"]
    subject: tuple[str, ...]
    predicate: str | None
    operator: Operator | None
    threshold: float | None
    interval_low: float | None
    interval_low_inclusive: bool
    interval_high: float | None
    interval_high_inclusive: bool
    unit: str | None
    time_start: datetime | None
    time_end: datetime | None
    competition: str | None
    event_scope: str | None
    jurisdiction: str | None
    stage: str | None
    singular_winner: bool
    resolution_signature: str
    numeric_predicate_signature: str | None
    temporal_predicate_signature: str | None
    stage_family_signature: str | None
    winner_family_signature: str | None
    proof_scope_key: str
    rule_applicability_fingerprint: str
    spans: tuple[SourceSpan, ...]

    def spans_json(self) -> str:
        return json.dumps(
            [span.__dict__ for span in self.spans],
            sort_keys=True,
            separators=(",", ":"),
        )


_COMPARISON = re.compile(
    r"\b(?P<phrase>at least|no less than|more than|over|above|greater than|"
    r"at most|no more than|less than|under|below|exactly|equal to|reach|"
    r"reaches|hit|hits|dip to|dips to|fall to|falls to|drop to|drops to)\s+"
    r"(?P<currency>[$€£])?\s*(?P<number>-?\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<percent>%|percent|percentage)?",
    re.IGNORECASE,
)
_BETWEEN = re.compile(
    r"\bbetween\s+(?P<currency>[$€£])?\s*(?P<low>\d[\d,]*(?:\.\d+)?)"
    r"\s+(?:and|to|-)\s+(?P<high>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<percent>%|percent|percentage)?",
    re.IGNORECASE,
)
_DEADLINE = re.compile(
    r"\b(?:by|before|on or before)\s+(?P<date>"
    r"\d{4}-\d{2}-\d{2}|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4})\b",
    re.IGNORECASE,
)
_STAGES: tuple[tuple[str, str], ...] = (
    ("round of 32", "round of 32"),
    ("round of 16", "round of 16"),
    ("quarter-finals", "quarterfinal"),
    ("quarterfinals", "quarterfinal"),
    ("quarter-final", "quarterfinal"),
    ("quarterfinal", "quarterfinal"),
    ("semi-finals", "semifinal"),
    ("semifinals", "semifinal"),
    ("semi-final", "semifinal"),
    ("semifinal", "semifinal"),
    ("the final", "final"),
    ("win the", "winner"),
    ("winner", "winner"),
)
_SINGULAR_WINNER = re.compile(
    r"^will\s+(?P<subject>.+?)\s+(?:win|be the winner of)\s+(?P<event>.+?)\??$",
    re.IGNORECASE,
)
_NON_SINGULAR = re.compile(
    r"\b(top\s*\d+|top[- ]?n|podium|qualif(?:y|ication)|advance|"
    r"medal|most|any|at least|tie|draw|reach the|make the)\b",
    re.IGNORECASE,
)


def extract_proposition(
    market: SourceMarket,
    outcome: SourceOutcome,
) -> ExtractedProposition:
    question = unicodedata.normalize("NFKC", market.question).strip()
    outcome_text = unicodedata.normalize("NFKC", outcome.outcome).strip()
    spans: list[SourceSpan] = []
    polarity: Literal["positive", "negative"] = (
        "negative" if outcome_text.casefold() in {"no", "false"} else "positive"
    )
    spans.append(SourceSpan("outcome", 0, len(outcome.outcome), outcome.outcome))

    operator: Operator | None = None
    threshold: float | None = None
    interval_low: float | None = None
    interval_low_inclusive = False
    interval_high: float | None = None
    interval_high_inclusive = False
    unit: str | None = None
    (
        comparison,
        bounded,
        deadline_match,
        stage,
        winner_match,
        normalized_question,
        normalized_description,
    ) = _question_analysis(question, market.description)
    ambiguous = comparison is not None and bounded is not None
    if comparison is not None and not ambiguous:
        phrase = comparison.group("phrase").casefold()
        operator = _operator_for(phrase)
        threshold = float(comparison.group("number").replace(",", ""))
        unit = _unit_for(comparison.group("currency"), comparison.group("percent"))
        spans.append(
            SourceSpan("question", comparison.start(), comparison.end(), comparison.group(0))
        )
    elif bounded is not None and not ambiguous:
        interval_low = float(bounded.group("low").replace(",", ""))
        interval_high = float(bounded.group("high").replace(",", ""))
        interval_low_inclusive = True
        interval_high_inclusive = True
        unit = _unit_for(bounded.group("currency"), bounded.group("percent"))
        spans.append(SourceSpan("question", bounded.start(), bounded.end(), bounded.group(0)))

    parsed_deadline = _deadline_utc(deadline_match.group("date")) if deadline_match else None
    if deadline_match is not None and parsed_deadline is not None:
        spans.append(
            SourceSpan(
                "question",
                deadline_match.start(),
                deadline_match.end(),
                deadline_match.group(0),
            )
        )

    if stage is not None:
        stage_start = question.casefold().find(stage[0].casefold())
        if stage_start >= 0:
            spans.append(
                SourceSpan(
                    "question",
                    stage_start,
                    stage_start + len(stage[0]),
                    question[stage_start : stage_start + len(stage[0])],
                )
            )
        stage_value = stage[1]
    else:
        stage_value = None

    singular_winner = bool(winner_match and not _NON_SINGULAR.search(question))
    subject_value: str | None = None
    if winner_match is not None:
        subject_value = normalize_text(winner_match.group("subject"))
        subject_start = winner_match.start("subject")
        spans.append(
            SourceSpan(
                "question",
                subject_start,
                winner_match.end("subject"),
                winner_match.group("subject"),
            )
        )
    elif comparison is not None:
        prefix = question[: comparison.start()].strip(" ?,:-")
        prefix = re.sub(r"^(will|does|is|can)\s+", "", prefix, flags=re.IGNORECASE)
        subject_value = normalize_text(prefix) or None

    normalized_outcome = normalize_text(outcome_text).casefold()
    resolution_signature = canonical_json_sha256(
        {
            "question": normalized_question,
            "outcome": normalized_outcome,
            "description": normalized_description,
            "time_start": _time_value(market.time_start),
            "time_end": _time_value(market.time_end),
        }
    )
    numeric_signature = None
    numeric_match = comparison if operator is not None else bounded
    if numeric_match is not None and not ambiguous:
        # Match offsets belong to ``question``. Build the placeholder there
        # before normalization so compatibility characters or collapsed
        # whitespace cannot shift the template boundary.
        template = normalize_text(
            question[: numeric_match.start()]
            + " <numeric-threshold> "
            + question[numeric_match.end() :]
        ).casefold()
        numeric_signature = canonical_json_sha256(
            {
                "template": template,
                "unit": unit,
                "outcome": normalized_outcome,
                "time_start": _time_value(market.time_start),
                "time_end": _time_value(market.time_end),
            }
        )
    effective_time_start = _utc(market.time_start)
    effective_time_end = _utc(market.time_end) or parsed_deadline
    temporal_template = normalized_question
    if deadline_match is not None and parsed_deadline is not None:
        temporal_template = normalize_text(
            question[: deadline_match.start()]
            + " <deadline> "
            + question[deadline_match.end() :]
        ).casefold()
    temporal_signature = (
        canonical_json_sha256(
            {"question": temporal_template, "outcome": normalized_outcome}
        )
        if effective_time_start is not None or effective_time_end is not None
        else None
    )
    competition = normalize_text(market.event_slug or "") or None
    event_scope = normalize_text(market.event_id or market.event_slug or "") or None
    stage_family = (
        canonical_json_sha256(
            {
                "question_without_stage": _without_stage(normalized_question),
                "competition": competition,
                "outcome": normalized_outcome,
            }
        )
        if stage_value is not None
        else None
    )
    winner_family = (
        canonical_json_sha256(
            {
                "event": normalize_text(winner_match.group("event")).casefold(),
                "event_scope": event_scope,
            }
        )
        if singular_winner and winner_match is not None
        else None
    )
    proof_scope_key = canonical_json_sha256(
        {
            "version": PROOF_SCOPE_VERSION,
            "numeric_signature": numeric_signature,
            "temporal_signature": temporal_signature,
            "stage_family": stage_family,
            "winner_family": winner_family,
            "competition": competition,
            "jurisdiction": None,
            "resolution_signature": resolution_signature,
        }
    )
    meaningful = any(
        value is not None
        for value in (
            operator,
            interval_low,
            stage_value,
            winner_family,
            temporal_signature,
        )
    )
    status: ExtractionStatus = (
        "ambiguous" if ambiguous else "exact" if meaningful else "unmatched"
    )
    applicability = canonical_json_sha256(
        {
            "version": RULE_APPLICABILITY_VERSION,
            "status": status,
            "binary": len(market.outcomes) == 2,
            "categorical": len(market.outcomes) > 2,
            "numeric": numeric_signature,
            "temporal": temporal_signature,
            "stage": stage_family,
            "singular_winner": winner_family,
        }
    )
    return ExtractedProposition(
        status=status,
        polarity=polarity,
        subject=(subject_value,) if subject_value else (),
        predicate=normalized_question or None,
        operator=operator,
        threshold=threshold,
        interval_low=interval_low,
        interval_low_inclusive=interval_low_inclusive,
        interval_high=interval_high,
        interval_high_inclusive=interval_high_inclusive,
        unit=unit,
        time_start=effective_time_start,
        time_end=effective_time_end,
        competition=competition,
        event_scope=event_scope,
        jurisdiction=None,
        stage=stage_value,
        singular_winner=singular_winner,
        resolution_signature=resolution_signature,
        numeric_predicate_signature=numeric_signature,
        temporal_predicate_signature=temporal_signature,
        stage_family_signature=stage_family,
        winner_family_signature=winner_family,
        proof_scope_key=proof_scope_key,
        rule_applicability_fingerprint=applicability,
        spans=tuple(spans),
    )


def _operator_for(phrase: str) -> Operator:
    if phrase in {"at least", "no less than", "reach", "reaches", "hit", "hits"}:
        return "greater_than_or_equal"
    if phrase in {"more than", "over", "above", "greater than"}:
        return "greater_than"
    if phrase in {"at most", "no more than", "dip to", "dips to", "fall to", "falls to", "drop to", "drops to"}:
        return "less_than_or_equal"
    if phrase in {"less than", "under", "below"}:
        return "less_than"
    return "equal"


def _unit_for(currency: str | None, percent: str | None) -> str | None:
    if currency == "$":
        return "USD"
    if currency == "€":
        return "EUR"
    if currency == "£":
        return "GBP"
    return "percent" if percent else None


def _stage(question: str) -> tuple[str, str] | None:
    lowered = question.casefold()
    matches = [(literal, value) for literal, value in _STAGES if literal in lowered]
    return matches[0] if len(matches) == 1 else None


@lru_cache(maxsize=8_192)
def _question_analysis(
    question: str,
    description: str,
) -> tuple[
    re.Match[str] | None,
    re.Match[str] | None,
    re.Match[str] | None,
    tuple[str, str] | None,
    re.Match[str] | None,
    str,
    str,
]:
    return (
        _COMPARISON.search(question),
        _BETWEEN.search(question),
        _DEADLINE.search(question),
        _stage(question),
        _SINGULAR_WINNER.fullmatch(question),
        normalize_text(question).casefold(),
        normalize_text(description).casefold(),
    )


def _without_stage(value: str) -> str:
    result = value
    for literal, _ in _STAGES:
        result = result.replace(literal, "<stage>")
    return result


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _deadline_utc(value: str) -> datetime | None:
    cleaned = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", value.strip())
    cleaned = cleaned.replace(",", "")
    formats = ("%Y-%m-%d", "%B %d %Y", "%b %d %Y")
    for pattern in formats:
        try:
            parsed = datetime.strptime(cleaned, pattern)
            return datetime.combine(parsed.date(), time.max, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _time_value(value: datetime | None) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def extractor_identity() -> tuple[str, str]:
    return EXTRACTOR_ID, EXTRACTOR_VERSION
