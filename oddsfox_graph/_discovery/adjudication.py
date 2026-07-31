from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .contracts import AtomicPairAssessment


def nli_text(proposition: Mapping[str, Any]) -> str:
    return " | ".join(
        str(proposition.get(field) or "")
        for field in (
            "question",
            "description",
            "outcome",
            "subject",
            "predicate",
            "operator",
            "threshold",
            "unit",
            "time_start",
            "time_end",
            "event_scope",
            "polarity",
        )
    )


def derive_atomic_relation(
    assessment: AtomicPairAssessment,
) -> tuple[str | None, str | None]:
    judgments = (
        assessment.a_implies_b,
        assessment.b_implies_a,
        assessment.can_both_be_true,
        assessment.must_one_be_true,
        assessment.logically_related,
    )
    if "unknown" in judgments:
        return "uncertain", None
    a_to_b = assessment.a_implies_b == "yes"
    b_to_a = assessment.b_implies_a == "yes"
    co_possible = assessment.can_both_be_true == "yes"
    exhaustive = assessment.must_one_be_true == "yes"
    related = assessment.logically_related == "yes"
    if (a_to_b or b_to_a) and not co_possible:
        return None, "entailment contradicts the claim that both cannot be true"
    if (a_to_b or b_to_a or exhaustive or not co_possible) and not related:
        return None, (
            "positive logical judgments contradict logically_related=no"
        )
    if a_to_b and b_to_a:
        return "equivalent", None
    if not a_to_b and not b_to_a and not co_possible:
        return ("complement" if exhaustive else "mutually_exclusive"), None
    if a_to_b != b_to_a:
        return ("A_implies_B" if a_to_b else "B_implies_A"), None
    if not a_to_b and not b_to_a and co_possible and related:
        return "compatible", None
    if not a_to_b and not b_to_a and not related and not exhaustive:
        return "unrelated", None
    return None, "atomic judgments do not map to a consistent relation"


def classification_validation_error(
    classification: AtomicPairAssessment,
    proposition_a: Mapping[str, Any],
    proposition_b: Mapping[str, Any],
) -> str | None:
    all_support = classification.supporting_fields
    if classification.unsupported_assumption:
        return "classification declares an unsupported assumption"
    if classification.assumptions and not classification.supporting_fields:
        return (
            "classification contains assumptions without "
            "supporting-field citations"
        )
    relation, relation_error = derive_atomic_relation(classification)
    if relation_error:
        return relation_error
    if relation not in {"unrelated", "uncertain"} and not all_support:
        return "positive classifications require supporting-field citations"
    for citation in all_support:
        proposition = (
            proposition_a if citation.proposition == "A" else proposition_b
        )
        raw_value = proposition.get(citation.field)
        if raw_value in (None, "", []):
            return (
                f"supporting field {citation.proposition}.{citation.field} "
                "is empty in the supplied proposition"
            )
        supplied_values = {
            str(raw_value).strip().casefold(),
            json.dumps(
                raw_value,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).strip().casefold(),
        }
        if isinstance(raw_value, (list, tuple, set)):
            supplied_values.update(
                str(item).strip().casefold() for item in raw_value
            )
        if citation.value.strip().casefold() not in supplied_values:
            return (
                f"supporting value for {citation.proposition}.{citation.field} "
                "does not exactly match the supplied proposition"
            )
    return None
