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
    "jurisdiction",
    "polarity",
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

    if proposition_signature(a) == proposition_signature(b):
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

    a_stage = stage_rank(a)
    b_stage = stage_rank(b)
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
    a: dict[str, Any],
    b: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if a.get("threshold") is None or b.get("threshold") is None:
        return None
    if a.get("operator") != b.get("operator"):
        return None
    if not _same_except(a, b, {"threshold"}):
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
    return bool(set(predicate.replace("-", " ").split()) & winner_words) or (
        object_ in winner_words
    )


def same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_event = a.get("event_id") or a.get("event_slug")
    b_event = b.get("event_id") or b.get("event_slug")
    return bool(a_event and a_event == b_event)


def times_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_start, a_end = a.get("time_start"), a.get("time_end")
    b_start, b_end = b.get("time_start"), b.get("time_end")
    return bool(
        a_start
        and a_end
        and b_start
        and b_end
        and a_start <= b_end
        and b_start <= a_end
    )


def hashable(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())
