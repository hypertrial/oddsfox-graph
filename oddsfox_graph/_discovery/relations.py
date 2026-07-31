from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any


SEMANTIC_KEYS = (
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
)

RULE_REGISTRY = {
    "same_market.binary_complement.v1": {
        "version": "1",
        "basis": "same_market",
        "applicability": "same_market_binary_yes_no",
        "hard_fact": True,
    },
    "same_market.categorical_exclusion.v1": {
        "version": "1",
        "basis": "same_market",
        "applicability": "same_market_distinct_outcomes",
        "hard_fact": True,
    },
    "equivalence.normalized_fields.v1": {
        "version": "1",
        "basis": "normalized_equivalence",
        "applicability": "same_normalized_fields_and_authoritative_scope",
        "hard_fact": False,
    },
    "threshold.interval_containment.v2": {
        "version": "2",
        "basis": "numeric_threshold",
        "applicability": "same_scope_interval_set_containment",
        "hard_fact": False,
    },
    "time.interval_containment.v1": {
        "version": "1",
        "basis": "time_window_containment",
        "applicability": "same_scope_time_interval_containment",
        "hard_fact": False,
    },
    "tournament.stage_progression.v1": {
        "version": "1",
        "basis": "tournament_stage",
        "applicability": "same_scope_positive_tournament_progression",
        "hard_fact": False,
    },
    "event.single_winner.v1": {
        "version": "1",
        "basis": "single_winner",
        "applicability": "same_authoritative_single_winner_event",
        "hard_fact": False,
    },
}

HARD_FACT_RULE_IDS = frozenset(
    rule_id
    for rule_id, metadata in RULE_REGISTRY.items()
    if metadata["hard_fact"]
)

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


def deterministic_relation(
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
            "same_market.binary_complement.v1",
        )
    if same_market:
        return _rule(
            "mutually_exclusive",
            min(a_id, b_id),
            max(a_id, b_id),
            "same_market",
            "Distinct outcomes of one categorical market cannot both occur",
            1.0,
            "same_market.categorical_exclusion.v1",
        )

    if min(float(a["parse_confidence"]), float(b["parse_confidence"])) < parse_confidence:
        return None
    confidence = min(float(a["parse_confidence"]), float(b["parse_confidence"]))

    if (
        proposition_signature(a) == proposition_signature(b)
        and _same_authoritative_scope(a, b)
    ):
        return _rule(
            "equivalent",
            min(a_id, b_id),
            max(a_id, b_id),
            "normalized_equivalence",
            "Normalized proposition fields are identical",
            confidence,
            "equivalence.normalized_fields.v1",
        )

    threshold_relation = _numeric_threshold_relation(a, b)
    if threshold_relation:
        relation_type, src, dst = threshold_relation
        return _rule(
            relation_type,
            (
                min(str(src["proposition_id"]), str(dst["proposition_id"]))
                if relation_type == "equivalent"
                else str(src["proposition_id"])
            ),
            (
                max(str(src["proposition_id"]), str(dst["proposition_id"]))
                if relation_type == "equivalent"
                else str(dst["proposition_id"])
            ),
            "numeric_threshold",
            (
                "Normalized numeric predicates describe the same interval"
                if relation_type == "equivalent"
                else "A narrower numeric interval implies the containing interval"
            ),
            confidence,
            "threshold.interval_containment.v2",
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
            "time.interval_containment.v1",
        )

    a_stage = stage_rank(a)
    b_stage = stage_rank(b)
    if (
        a_stage is not None
        and b_stage is not None
        and a_stage != b_stage
        and _same_values(a, b, ("subject", "competition", "polarity"))
        and _same_authoritative_scope(a, b)
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
            "tournament.stage_progression.v1",
        )

    if (
        same_event(a, b)
        and is_winner_proposition(a)
        and is_winner_proposition(b)
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
            "event.single_winner.v1",
        )
    return None


def _rule(
    edge_type: str,
    src: str,
    dst: str,
    basis: str,
    explanation: str,
    confidence: float,
    rule_id: str,
) -> dict[str, Any]:
    return {
        "edge_type": edge_type,
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_basis": basis,
        "explanation": explanation,
        "confidence": confidence,
        "rule_id": rule_id,
    }


def _numeric_threshold_relation(
    a: dict[str, Any],
    b: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    if a.get("threshold") is None or b.get("threshold") is None:
        return None
    if a.get("operator") not in {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    } or b.get("operator") not in {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    }:
        return None
    if not _same_except(a, b, {"operator", "threshold", "polarity"}):
        return None
    if a.get("polarity") != b.get("polarity"):
        return None
    if not _same_authoritative_scope(a, b):
        return None
    a_interval = _numeric_interval(a)
    b_interval = _numeric_interval(b)
    a_subset_b = _interval_subset(a_interval, b_interval)
    b_subset_a = _interval_subset(b_interval, a_interval)
    if a_subset_b and b_subset_a:
        return "equivalent", a, b
    if a_subset_b:
        return "implies", a, b
    if b_subset_a:
        return "implies", b, a
    return None


def _numeric_interval(
    proposition: dict[str, Any],
) -> tuple[tuple[float | None, bool], tuple[float | None, bool]]:
    operator = str(proposition["operator"])
    if proposition.get("polarity") == "negative":
        operator = {
            "greater_than": "less_than_or_equal",
            "greater_than_or_equal": "less_than",
            "less_than": "greater_than_or_equal",
            "less_than_or_equal": "greater_than",
        }[operator]
    threshold = float(proposition["threshold"])
    if operator == "greater_than":
        return (threshold, False), (None, False)
    if operator == "greater_than_or_equal":
        return (threshold, True), (None, False)
    if operator == "less_than":
        return (None, False), (threshold, False)
    return (None, False), (threshold, True)


def _interval_subset(
    inner: tuple[tuple[float | None, bool], tuple[float | None, bool]],
    outer: tuple[tuple[float | None, bool], tuple[float | None, bool]],
) -> bool:
    (inner_low, inner_low_inclusive), (inner_high, inner_high_inclusive) = inner
    (outer_low, outer_low_inclusive), (outer_high, outer_high_inclusive) = outer
    lower_ok = (
        outer_low is None
        or (
            inner_low is not None
            and (
                inner_low > outer_low
                or (
                    inner_low == outer_low
                    and (outer_low_inclusive or not inner_low_inclusive)
                )
            )
        )
    )
    upper_ok = (
        outer_high is None
        or (
            inner_high is not None
            and (
                inner_high < outer_high
                or (
                    inner_high == outer_high
                    and (outer_high_inclusive or not inner_high_inclusive)
                )
            )
        )
    )
    return lower_ok and upper_ok


def _time_window_relation(
    a: dict[str, Any],
    b: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not all(
        (
            a.get("time_start"),
            a.get("time_end"),
            b.get("time_start"),
            b.get("time_end"),
        )
    ):
        return None
    if not _same_except(a, b, {"time_start", "time_end"}):
        return None
    if not _same_authoritative_scope(a, b):
        return None
    a_contains_b = (
        a["time_start"] <= b["time_start"]
        and a["time_end"] >= b["time_end"]
    )
    b_contains_a = (
        b["time_start"] <= a["time_start"]
        and b["time_end"] >= a["time_end"]
    )
    if a_contains_b and not b_contains_a:
        relation = (b, a)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    if b_contains_a and not a_contains_b:
        relation = (a, b)
        return relation[::-1] if a.get("polarity") == "negative" else relation
    return None


def proposition_signature(proposition: dict[str, Any]) -> tuple[object, ...]:
    return tuple(hashable(proposition.get(key)) for key in SEMANTIC_KEYS)


def _same_except(
    a: dict[str, Any],
    b: dict[str, Any],
    excluded: set[str],
) -> bool:
    return all(
        hashable(a.get(key)) == hashable(b.get(key))
        for key in SEMANTIC_KEYS
        if key not in excluded
    )


def _same_values(
    a: dict[str, Any],
    b: dict[str, Any],
    keys: Sequence[str],
) -> bool:
    return all(hashable(a.get(key)) == hashable(b.get(key)) for key in keys)


def stage_rank(proposition: dict[str, Any]) -> int | None:
    values = [proposition.get("object"), proposition.get("predicate")]
    for value in values:
        if not value:
            continue
        normalized = normalize_text(str(value)).casefold()
        if normalized in _STAGE_RANKS:
            return _STAGE_RANKS[normalized]
        for name, rank in _STAGE_RANKS.items():
            if name in normalized:
                return rank
    return None


def is_winner_proposition(proposition: dict[str, Any]) -> bool:
    predicate = normalize_text(str(proposition.get("predicate") or "")).casefold()
    object_ = normalize_text(str(proposition.get("object") or "")).casefold()
    winner_words = {"win", "winner", "winners", "winning", "wins"}
    winner_language = bool(
        set(predicate.replace("-", " ").split()) & winner_words
    ) or (
        object_ in winner_words
    )
    scope = normalize_text(
        str(proposition.get("event_scope") or "")
    ).casefold().replace("_", " ")
    single_winner_scope = scope in {
        "one winner",
        "single winner",
        "winner takes all",
    }
    return winner_language and single_winner_scope


def same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_event_id = a.get("event_id")
    b_event_id = b.get("event_id")
    if a_event_id and b_event_id and a_event_id == b_event_id:
        return True
    a_event_slug = a.get("event_slug")
    b_event_slug = b.get("event_slug")
    return bool(
        a_event_slug
        and b_event_slug
        and a_event_slug == b_event_slug
    )


def _same_authoritative_scope(
    a: dict[str, Any],
    b: dict[str, Any],
) -> bool:
    if same_event(a, b):
        return True
    a_scope = normalize_text(str(a.get("event_scope") or "")).casefold()
    b_scope = normalize_text(str(b.get("event_scope") or "")).casefold()
    return bool(a_scope and a_scope == b_scope)


def hashable(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())
