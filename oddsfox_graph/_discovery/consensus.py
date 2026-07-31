"""Pure dual-model consensus rules used by qualification and discovery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .relation_logic import classification_validation_error, derive_atomic_relation
from .contracts import AtomicPairAssessment, ParsedMarket, ParsedOutcome, SourceMarket
from .input import utc_datetime
from .parsing import canonical_entity, canonical_unit, normalize_optional
from .provenance import canonical_json_sha256
from .relations import normalize_text


PARSE_ASSESSMENT_COLUMNS = {
    "assessment_id": "VARCHAR",
    "proposition_id": "VARCHAR",
    "market_id": "VARCHAR",
    "model_role": "VARCHAR",
    "model_version": "VARCHAR",
    "inference_fingerprint": "VARCHAR",
    "status": "VARCHAR",
    "confidence": "DOUBLE",
    "parsed_json": "VARCHAR",
    "citations": "VARCHAR[]",
    "validation_error": "VARCHAR",
    "authoritative_conflicts": "VARCHAR[]",
}

MODEL_ASSESSMENT_COLUMNS = {
    "assessment_id": "VARCHAR",
    "pair_id": "VARCHAR",
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "model_role": "VARCHAR",
    "model_version": "VARCHAR",
    "inference_fingerprint": "VARCHAR",
    "relation": "VARCHAR",
    "confidence": "DOUBLE",
    "atomic_a_implies_b": "VARCHAR",
    "atomic_b_implies_a": "VARCHAR",
    "atomic_can_both_be_true": "VARCHAR",
    "atomic_must_one_be_true": "VARCHAR",
    "atomic_logically_related": "VARCHAR",
    "supporting_fields_json": "VARCHAR",
    "assumptions": "VARCHAR[]",
    "unsupported_assumption": "BOOLEAN",
    "requires_review": "BOOLEAN",
    "status": "VARCHAR",
    "validation_error": "VARCHAR",
}

QUARANTINE_COLUMNS = {
    "quarantine_id": "VARCHAR",
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "stage": "VARCHAR",
    "reason_code": "VARCHAR",
    "proposed_relation": "VARCHAR",
    "confidence": "DOUBLE",
    "primary_relation": "VARCHAR",
    "verifier_relation": "VARCHAR",
    "explanation": "VARCHAR",
    "primary_model_version": "VARCHAR",
    "verifier_model_version": "VARCHAR",
    "primary_inference_fingerprint": "VARCHAR",
    "verifier_inference_fingerprint": "VARCHAR",
    "automation_profile_id": "VARCHAR",
}


@dataclass(frozen=True)
class OutcomeConsensus:
    parsed: ParsedOutcome | None
    status: str
    disagreements: tuple[str, ...]


@dataclass(frozen=True)
class RelationConsensus:
    relation: str | None
    confidence: float | None
    status: str
    reason: str | None


def merge_parsed_markets(
    source: SourceMarket,
    primary: ParsedMarket | None,
    verifier: ParsedMarket | None,
) -> dict[str, OutcomeConsensus]:
    primary_by_outcome = {
        row.outcome: row for row in (primary.propositions if primary else [])
    }
    verifier_by_outcome = {
        row.outcome: row for row in (verifier.propositions if verifier else [])
    }
    result: dict[str, OutcomeConsensus] = {}
    for outcome in source.outcomes:
        first = primary_by_outcome.get(outcome.outcome)
        second = verifier_by_outcome.get(outcome.outcome)
        if first is None or second is None:
            result[outcome.outcome] = OutcomeConsensus(
                parsed=None,
                status="missing_model_parse",
                disagreements=("one or both models omitted the outcome",),
            )
            continue
        first_values = _normalized_parse(first)
        second_values = _normalized_parse(second)
        disagreements = tuple(
            field
            for field in sorted(first_values)
            if first_values[field] != second_values[field]
        )
        if disagreements:
            result[outcome.outcome] = OutcomeConsensus(
                parsed=None,
                status="model_disagreement",
                disagreements=disagreements,
            )
            continue
        merged = first.model_copy(
            update={"parse_confidence": min(first.parse_confidence, second.parse_confidence)}
        )
        result[outcome.outcome] = OutcomeConsensus(
            parsed=merged,
            status="agreed",
            disagreements=(),
        )
    return result


def relation_consensus(
    primary: AtomicPairAssessment | None,
    verifier: AtomicPairAssessment | None,
    proposition_a: Mapping[str, Any],
    proposition_b: Mapping[str, Any],
    *,
    nli_veto: bool,
) -> RelationConsensus:
    if primary is None or verifier is None:
        return RelationConsensus(None, None, "inference_failure", "missing assessment")
    if primary.unsupported_assumption or verifier.unsupported_assumption:
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "assumption",
            "one or both models declare an unsupported assumption",
        )
    if primary.assumptions or verifier.assumptions:
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "assumption",
            "model-derived relations require empty assumption lists",
        )
    if primary.requires_review or verifier.requires_review:
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "inference_failure",
            "one or both models requested quarantine",
        )
    primary_error = classification_validation_error(primary, proposition_a, proposition_b)
    verifier_error = classification_validation_error(verifier, proposition_a, proposition_b)
    if primary_error or verifier_error:
        error = primary_error or verifier_error
        assert error is not None
        citation_error = any(
            phrase in error
            for phrase in (
                "supporting-field citations",
                "supporting field",
                "supporting value",
            )
        )
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "invalid_citation" if citation_error else "inference_failure",
            error,
        )
    first_relation, first_error = derive_atomic_relation(primary)
    second_relation, second_error = derive_atomic_relation(verifier)
    if first_error or second_error:
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "invalid_assessment",
            first_error or second_error,
        )
    if first_relation != second_relation:
        return RelationConsensus(
            None,
            min(primary.confidence, verifier.confidence),
            "model_disagreement",
            f"primary={first_relation}; verifier={second_relation}",
        )
    if first_relation in {"uncertain", "unrelated"}:
        return RelationConsensus(
            first_relation,
            min(primary.confidence, verifier.confidence),
            first_relation,
            None,
        )
    if nli_veto:
        return RelationConsensus(
            first_relation,
            min(primary.confidence, verifier.confidence),
            "nli_veto",
            "bidirectional NLI contradicts the generative consensus",
        )
    return RelationConsensus(
        first_relation,
        min(primary.confidence, verifier.confidence),
        "agreed",
        None,
    )


def nli_contradicts(relation: str | None, candidate: Mapping[str, Any]) -> bool:
    if relation in {None, "unrelated", "uncertain"}:
        return False
    a_entailment = float(candidate.get("nli_a_to_b_entailment") or 0.0)
    b_entailment = float(candidate.get("nli_b_to_a_entailment") or 0.0)
    a_contradiction = float(candidate.get("nli_a_to_b_contradiction") or 0.0)
    b_contradiction = float(candidate.get("nli_b_to_a_contradiction") or 0.0)
    if relation == "A_implies_B":
        return a_contradiction >= 0.90 or a_entailment <= 0.05
    if relation == "B_implies_A":
        return b_contradiction >= 0.90 or b_entailment <= 0.05
    if relation == "equivalent":
        return (
            a_contradiction >= 0.90
            or b_contradiction >= 0.90
            or a_entailment <= 0.05
            or b_entailment <= 0.05
        )
    if relation in {"complement", "mutually_exclusive"}:
        return a_entailment >= 0.90 or b_entailment >= 0.90
    return a_contradiction >= 0.90 or b_contradiction >= 0.90


def parse_assessment_row(
    *,
    proposition_id: str,
    market_id: str,
    role: str,
    model: str,
    fingerprint: str,
    parsed: ParsedOutcome | None,
    error: str | None,
    authoritative_conflicts: Sequence[str] = (),
) -> dict[str, Any]:
    conflict_values = list(authoritative_conflicts)
    validation_error = error or (
        "; ".join(conflict_values) if conflict_values else None
    )
    content = {
        "proposition_id": proposition_id,
        "market_id": market_id,
        "model_role": role,
        "model_version": model,
        "inference_fingerprint": fingerprint,
        "status": (
            "valid"
            if parsed is not None and validation_error is None
            else "invalid"
        ),
        "confidence": parsed.parse_confidence if parsed is not None else 0.0,
        "parsed_json": (
            json.dumps(parsed.model_dump(mode="json"), sort_keys=True, default=str)
            if parsed is not None
            else None
        ),
        "citations": list(parsed.citations) if parsed is not None else [],
        "validation_error": validation_error,
        "authoritative_conflicts": conflict_values,
    }
    return {"assessment_id": canonical_json_sha256(content), **content}


def model_assessment_row(
    *,
    proposition_a_id: str,
    proposition_b_id: str,
    role: str,
    model: str,
    fingerprint: str,
    assessment: AtomicPairAssessment | None,
    validation_error: str | None,
) -> dict[str, Any]:
    relation = None
    relation_error = None
    if assessment is not None:
        relation, relation_error = derive_atomic_relation(assessment)
    content = {
        "pair_id": (
            assessment.pair_id
            if assessment is not None
            else canonical_json_sha256([proposition_a_id, proposition_b_id])
        ),
        "proposition_a_id": proposition_a_id,
        "proposition_b_id": proposition_b_id,
        "model_role": role,
        "model_version": model,
        "inference_fingerprint": fingerprint,
        "relation": relation,
        "confidence": assessment.confidence if assessment is not None else 0.0,
        "atomic_a_implies_b": assessment.a_implies_b if assessment else None,
        "atomic_b_implies_a": assessment.b_implies_a if assessment else None,
        "atomic_can_both_be_true": assessment.can_both_be_true if assessment else None,
        "atomic_must_one_be_true": assessment.must_one_be_true if assessment else None,
        "atomic_logically_related": assessment.logically_related if assessment else None,
        "supporting_fields_json": (
            json.dumps(
                [row.model_dump(mode="json") for row in assessment.supporting_fields],
                sort_keys=True,
            )
            if assessment is not None
            else None
        ),
        "assumptions": list(assessment.assumptions) if assessment else [],
        "unsupported_assumption": (
            assessment.unsupported_assumption if assessment else False
        ),
        "requires_review": assessment.requires_review if assessment else True,
        "status": "valid" if assessment is not None and not validation_error and not relation_error else "invalid",
        "validation_error": validation_error or relation_error,
    }
    return {"assessment_id": canonical_json_sha256(content), **content}


def quarantine_row(
    *,
    proposition_a_id: str,
    proposition_b_id: str | None,
    stage: str,
    reason_code: str,
    proposed_relation: str | None,
    confidence: float | None,
    primary_relation: str | None,
    verifier_relation: str | None,
    explanation: str,
    primary_model: str,
    verifier_model: str,
    primary_fingerprint: str,
    verifier_fingerprint: str,
    automation_profile_id: str | None,
) -> dict[str, Any]:
    content = {
        "proposition_a_id": proposition_a_id,
        "proposition_b_id": proposition_b_id,
        "stage": stage,
        "reason_code": reason_code,
        "proposed_relation": proposed_relation,
        "confidence": confidence,
        "primary_relation": primary_relation,
        "verifier_relation": verifier_relation,
        "explanation": explanation,
        "primary_model_version": primary_model,
        "verifier_model_version": verifier_model,
        "primary_inference_fingerprint": primary_fingerprint,
        "verifier_inference_fingerprint": verifier_fingerprint,
        "automation_profile_id": automation_profile_id,
    }
    return {"quarantine_id": canonical_json_sha256(content), **content}


def _normalized_parse(parsed: ParsedOutcome) -> dict[str, object]:
    return {
        "outcome": parsed.outcome,
        "subject": tuple(
            sorted(
                canonical_entity(value)
                for value in parsed.subject
                if normalize_text(value)
            )
        ),
        "predicate": normalize_optional(parsed.predicate),
        "object": canonical_entity(parsed.object) if parsed.object else None,
        "operator": parsed.operator,
        "threshold": parsed.threshold,
        "unit": canonical_unit(parsed.unit) if parsed.unit else None,
        "time_start": utc_datetime(parsed.time_start),
        "time_end": utc_datetime(parsed.time_end),
        "competition": (
            canonical_entity(parsed.competition) if parsed.competition else None
        ),
        "event_scope": (
            canonical_entity(parsed.event_scope) if parsed.event_scope else None
        ),
        "jurisdiction": (
            canonical_entity(parsed.jurisdiction) if parsed.jurisdiction else None
        ),
        "polarity": parsed.polarity,
    }
