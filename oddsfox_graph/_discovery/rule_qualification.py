"""Independent generated-case qualification for deterministic rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class RuleQualificationCase:
    rule_id: str
    case_id: str
    proposition_a: dict[str, Any]
    proposition_b: dict[str, Any]
    expected_relation: str | None
    expected_src: str | None
    expected_dst: str | None


def qualify_rule_registry(
    registry: Mapping[str, Mapping[str, object]],
    evaluator: Callable[
        [dict[str, Any], dict[str, Any], float], dict[str, Any] | None
    ],
) -> dict[str, Any]:
    enabled: list[str] = []
    experimental: list[str] = []
    support: dict[str, dict[str, object]] = {}
    for rule_id, metadata in sorted(registry.items()):
        cases = generate_rule_qualification_cases(rule_id)
        positive = [case for case in cases if case.expected_relation is not None]
        adversarial = [case for case in cases if case.expected_relation is None]
        positive_passed = sum(
            _rule_case_passes(
                case,
                evaluator(case.proposition_a, case.proposition_b, 0.95),
            )
            for case in positive
        )
        adversarial_passed = sum(
            evaluator(case.proposition_a, case.proposition_b, 0.95) is None
            for case in adversarial
        )
        passed = (
            len(positive) >= 100
            and len(adversarial) >= 100
            and positive_passed == len(positive)
            and adversarial_passed == len(adversarial)
        )
        (enabled if passed else experimental).append(rule_id)
        support[rule_id] = {
            "source": (
                "authoritative_market_contract_and_independent_generated_cases"
                if bool(metadata.get("hard_fact"))
                else "independent_generated_cases"
            ),
            "positive": len(positive),
            "positive_passed": positive_passed,
            "adversarial": len(adversarial),
            "adversarial_passed": adversarial_passed,
            "passed": passed,
        }
    return {
        "qualification_kind": "independent_generated_cases",
        "minimum_positive_examples": 100,
        "minimum_adversarial_examples": 100,
        "enabled": enabled,
        "experimental": experimental,
        "support": support,
    }


def generate_rule_qualification_cases(
    rule_id: str,
) -> list[RuleQualificationCase]:
    cases: list[RuleQualificationCase] = []
    for index in range(100):
        a = _rule_proposition(f"{rule_id}-p-{index}-a", index * 2)
        b = _rule_proposition(f"{rule_id}-p-{index}-b", index * 2 + 1)
        negative_a = _rule_proposition(
            f"{rule_id}-n-{index}-a", 100 + index * 2
        )
        negative_b = _rule_proposition(
            f"{rule_id}-n-{index}-b", 101 + index * 2
        )
        if rule_id == "same_market.binary_complement.v1":
            expected = "complement"
            b["market_id"] = a["market_id"]
            negative_b["event_scope"] = "different-scope"
            negative_b["event_slug"] = "different-scope"
        elif rule_id == "same_market.categorical_exclusion.v1":
            expected = "mutually_exclusive"
            a["_expected_tokens"] = 3
            b["_expected_tokens"] = 3
            b["market_id"] = a["market_id"]
            negative_a["_expected_tokens"] = 3
            negative_b["_expected_tokens"] = 3
            negative_b["event_scope"] = "different-scope"
            negative_b["event_slug"] = "different-scope"
        elif rule_id == "equivalence.normalized_fields.v1":
            expected = "equivalent"
            negative_b["event_scope"] = "different-scope"
            negative_b["event_slug"] = "different-scope"
        elif rule_id == "threshold.interval_containment.v2":
            expected = "implies"
            a.update(operator="greater_than", threshold=100.0 + index, unit="USD")
            b.update(operator="greater_than", threshold=50.0 + index, unit="USD")
            negative_a.update(operator="greater_than", threshold=100.0, unit="USD")
            negative_b.update(operator="less_than", threshold=200.0, unit="USD")
        elif rule_id == "time.interval_containment.v1":
            expected = "implies"
            start = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=index
            )
            a.update(
                time_start=start + timedelta(days=2),
                time_end=start + timedelta(days=8),
            )
            b.update(time_start=start, time_end=start + timedelta(days=10))
            negative_a.update(
                time_start=start,
                time_end=start + timedelta(days=10),
            )
            negative_b.update(
                time_start=start + timedelta(days=5),
                time_end=start + timedelta(days=15),
            )
        elif rule_id == "tournament.stage_progression.v1":
            expected = "implies"
            a["object"] = "winner"
            b["object"] = "final"
            negative_a["object"] = "winner"
            negative_b["object"] = "final"
            negative_b["event_scope"] = "different-scope"
            negative_b["event_slug"] = "different-scope"
            negative_b["event_id"] = "different-event"
        elif rule_id == "event.single_winner.v1":
            expected = "mutually_exclusive"
            a.update(
                subject=[f"team-a-{index}"],
                predicate="win",
                event_scope="single winner",
            )
            b.update(
                subject=[f"team-b-{index}"],
                predicate="win",
                event_scope="single winner",
            )
            negative_a.update(
                subject=[f"medalist-a-{index}"],
                predicate="win",
                event_scope="multi winner",
                description="The event permits multiple winners.",
            )
            negative_b.update(
                subject=[f"medalist-b-{index}"],
                predicate="win",
                event_scope="multi winner",
                description="The event permits multiple winners.",
            )
        elif rule_id == "wc2026.same_progression.v1":
            expected = "equivalent"
            _wc2026_case(a, team=f"team-{index}", level=2, polarity="positive")
            _wc2026_case(b, team=f"team-{index}", level=2, polarity="positive")
            _wc2026_case(
                negative_a,
                team=f"team-{index}-a",
                level=2,
                polarity="positive",
            )
            _wc2026_case(
                negative_b,
                team=f"team-{index}-b",
                level=2,
                polarity="positive",
            )
        elif rule_id == "wc2026.progression.v1":
            expected = "implies"
            _wc2026_case(a, team=f"team-{index}", level=4, polarity="positive")
            _wc2026_case(b, team=f"team-{index}", level=2, polarity="positive")
            _wc2026_case(
                negative_a,
                team=f"team-{index}-a",
                level=4,
                polarity="positive",
            )
            _wc2026_case(
                negative_b,
                team=f"team-{index}-b",
                level=2,
                polarity="positive",
            )
        elif rule_id == "wc2026.winner_exclusion.v1":
            expected = "mutually_exclusive"
            _wc2026_case(a, team=f"team-{index}-a", level=5, polarity="positive")
            _wc2026_case(b, team=f"team-{index}-b", level=5, polarity="positive")
            _wc2026_case(
                negative_a,
                team=f"team-{index}-a",
                level=5,
                polarity="positive",
            )
            _wc2026_case(
                negative_b,
                team=f"team-{index}-b",
                level=5,
                polarity="negative",
            )
        else:
            raise ValueError(
                f"No independent qualification generator for {rule_id}"
            )
        expected_src = (
            str(a["proposition_id"])
            if expected == "implies"
            else min(str(a["proposition_id"]), str(b["proposition_id"]))
        )
        expected_dst = (
            str(b["proposition_id"])
            if expected == "implies"
            else max(str(a["proposition_id"]), str(b["proposition_id"]))
        )
        cases.append(
            RuleQualificationCase(
                rule_id,
                f"{rule_id}:positive:{index}",
                a,
                b,
                expected,
                expected_src,
                expected_dst,
            )
        )
        cases.append(
            RuleQualificationCase(
                rule_id,
                f"{rule_id}:adversarial:{index}",
                negative_a,
                negative_b,
                None,
                None,
                None,
            )
        )
    return cases


def _rule_case_passes(
    case: RuleQualificationCase,
    observed: dict[str, Any] | None,
) -> bool:
    return bool(
        observed is not None
        and observed.get("rule_id") == case.rule_id
        and observed.get("edge_type") == case.expected_relation
        and observed.get("src_node_id") == case.expected_src
        and observed.get("dst_node_id") == case.expected_dst
    )


def _wc2026_case(
    proposition: dict[str, Any],
    *,
    team: str,
    level: int,
    polarity: str,
) -> None:
    proposition.update(
        source_schema="polymarket-wc2026-graph-hourly-v1",
        team_name=team,
        subject=[team],
        progression_level=level,
        polarity=polarity,
        is_progression=polarity == "positive",
    )


def _rule_proposition(identifier: str, index: int) -> dict[str, Any]:
    return {
        "proposition_id": identifier,
        "market_id": f"generated-market-{index}",
        "event_id": "generated-event",
        "event_slug": "generated-scope",
        "outcome": "Yes",
        "question": "Will the generated logical event occur?",
        "description": "A controlled generated qualification case.",
        "_expected_tokens": 2,
        "subject": ["generated subject"],
        "predicate": "occur",
        "object": None,
        "operator": None,
        "threshold": None,
        "unit": None,
        "time_start": None,
        "time_end": None,
        "competition": "generated competition",
        "event_scope": "generated-scope",
        "jurisdiction": None,
        "polarity": "positive",
        "parse_confidence": 1.0,
    }
