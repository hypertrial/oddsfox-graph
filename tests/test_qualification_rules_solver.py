from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest

from oddsfox_graph._discovery.relation_logic import (
    classification_validation_error,
    derive_atomic_relation,
)
from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    SourceMarket,
    SourceOutcome,
)
from oddsfox_graph._discovery.relations import RULE_REGISTRY, deterministic_relation
from oddsfox_graph._discovery.solver import solve_proposals
from oddsfox_graph.qualification import (
    DOMAINS,
    PUBLISHABLE_RELATIONS,
    QualificationPrediction,
    assign_domain,
    evaluate_qualification,
    generate_qualification_cases,
    qualification_case_set_hash,
    qualification_retrieved_case_ids,
    qualify_rule_registry,
)


def _atomic(**changes: Any) -> AtomicPairAssessment:
    values: dict[str, Any] = {
        "pair_id": "pair",
        "a_implies_b": "no",
        "b_implies_a": "no",
        "can_both_be_true": "yes",
        "must_one_be_true": "no",
        "logically_related": "yes",
        "confidence": 0.99,
        "supporting_fields": [
            {"proposition": "A", "field": "question", "value": "A?"},
            {"proposition": "B", "field": "question", "value": "B?"},
        ],
        "assumptions": [],
        "unsupported_assumption": False,
        "requires_review": False,
    }
    values.update(changes)
    return AtomicPairAssessment.model_validate(values)


@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        ({"a_implies_b": "yes", "b_implies_a": "yes"}, "equivalent"),
        ({"can_both_be_true": "no", "must_one_be_true": "yes"}, "complement"),
        ({"can_both_be_true": "no"}, "mutually_exclusive"),
        ({"a_implies_b": "yes"}, "A_implies_B"),
        ({}, "compatible"),
        ({"logically_related": "no"}, "unrelated"),
        ({"a_implies_b": "unknown"}, "uncertain"),
    ),
)
def test_atomic_relation_mapping(changes: dict[str, Any], expected: str) -> None:
    relation, error = derive_atomic_relation(_atomic(**changes))
    assert error is None
    assert relation == expected


def test_atomic_contradictions_and_evidence_are_rejected() -> None:
    relation, error = derive_atomic_relation(
        _atomic(a_implies_b="yes", can_both_be_true="no")
    )
    assert relation is None
    assert "contradicts" in str(error)
    propositions = {
        "A": {"question": "A?"},
        "B": {"question": "B?"},
    }
    invalid = _atomic(
        supporting_fields=[
            {"proposition": "A", "field": "question", "value": "wrong"}
        ]
    )
    assert "does not exactly match" in str(
        classification_validation_error(invalid, propositions["A"], propositions["B"])
    )
    assumed = _atomic(assumptions=["same event"], supporting_fields=[])
    assert "assumptions" in str(
        classification_validation_error(assumed, propositions["A"], propositions["B"])
    )


def test_catalog_domain_assignment_uses_whole_words() -> None:
    assert assign_domain(_source("nba final")) == "sports"
    assert assign_domain(_source("presidential election")) == "elections"
    assert assign_domain(_source("bitcoin price")) == "cryptocurrency"
    assert assign_domain(_source("cpi inflation")) == "economic_indicators"
    assert assign_domain(_source("will launch by july")) == "date_based"
    assert assign_domain(_source("Ethiopian agreement")) == "other"


def test_qualification_generation_is_deterministic_market_disjoint_and_balanced() -> None:
    markets = _qualification_markets()
    first = generate_qualification_cases(markets)
    second = generate_qualification_cases(list(reversed(markets)))
    assert first == second
    assert qualification_case_set_hash(first) == qualification_case_set_hash(second)
    assert len(first) == 6_000
    parse_rows = [row for row in first if row["record_type"] == "parse"]
    pair_rows = [row for row in first if row["record_type"] == "pair"]
    assert len(parse_rows) == 1_000
    assert len(pair_rows) == 5_000
    assert {row["domain"] for row in parse_rows} == set(DOMAINS)
    selection_markets = {
        market
        for row in first
        if row["partition"] == "selection"
        for market in row["source_market_ids"]
    }
    validation_markets = {
        market
        for row in first
        if row["partition"] == "validation"
        for market in row["source_market_ids"]
    }
    assert selection_markets.isdisjoint(validation_markets)
    for relation in (*PUBLISHABLE_RELATIONS, "unrelated", "uncertain"):
        assert sum(row["expected_relation"] == relation for row in pair_rows) == (
            500 if relation in PUBLISHABLE_RELATIONS else 1_250
        )


def test_qualification_retrieval_uses_the_production_candidate_stage() -> None:
    cases = generate_qualification_cases(_qualification_markets())
    selected = [
        next(
            row
            for row in cases
            if row["record_type"] == "pair"
            and row["partition"] == "validation"
            and row["expected_relation"] == relation
        )
        for relation in PUBLISHABLE_RELATIONS
    ]

    def embed(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
        vectors = np.ones((len(texts), 4), dtype=np.float32)
        vectors[:, 1] = np.arange(len(texts), dtype=np.float32) + 1
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors

    retrieved = qualification_retrieved_case_ids(
        selected,
        DiscoveryConfig(top_k=5, max_candidates=1_000),
        embed,
    )
    assert retrieved == {str(row["case_id"]) for row in selected}


def test_qualification_threshold_selection_and_all_gates() -> None:
    cases = [
        {
            "case_id": f"parse-{index}",
            "record_type": "parse",
            "partition": "selection" if index < 3 else "validation",
        }
        for index in range(5)
    ]
    for relation in PUBLISHABLE_RELATIONS:
        for partition, count in (("selection", 300), ("validation", 200)):
            cases.extend(
                {
                    "case_id": f"{relation}-{partition}-{index}",
                    "record_type": "pair",
                    "partition": partition,
                    "expected_relation": relation,
                }
                for index in range(count)
            )
    predictions = [
        QualificationPrediction(
            case_id=str(row["case_id"]),
            record_type=row["record_type"],
            partition=row["partition"],
            expected_relation=row.get("expected_relation"),
            primary_structured_valid=True,
            verifier_structured_valid=True,
            id_coverage=True,
            authoritative_conflict=False,
            parse_agreed=True if row["record_type"] == "parse" else None,
            field_agreement={"subject": 1.0, "threshold": 1.0, "unit": 1.0, "time_start": 1.0, "time_end": 1.0},
            primary_relation=row.get("expected_relation"),
            verifier_relation=row.get("expected_relation"),
            consensus_relation=row.get("expected_relation"),
            consensus_confidence=0.99 if row["record_type"] == "pair" else None,
            citations_valid=True,
            assumptions_empty=True,
            nli_veto=False,
            stability_sampled=(
                row["record_type"] == "pair"
                and row["partition"] == "validation"
                and int(str(row["case_id"]).rsplit("-", 1)[1]) < 100
            ),
        )
        for row in cases
    ]
    evaluation = evaluate_qualification(cases, predictions)
    assert evaluation.status == "AUTOMATION_VALIDATED"
    assert all(evaluation.gates.values())
    broken = list(predictions)
    broken[-1] = broken[-1].model_copy(update={"assumptions_empty": False})
    assert evaluate_qualification(cases, broken).status == "QUALIFICATION_FAILED"


def test_same_market_facts_and_numeric_direction() -> None:
    yes = _proposition("yes", "m", "Yes")
    no = _proposition("no", "m", "No", polarity="negative")
    complement = deterministic_relation(yes, no, 0.95)
    assert complement is not None
    assert complement["edge_type"] == "complement"
    strict = _proposition("strict", "m1", "Yes", threshold=100.0)
    broad = _proposition("broad", "m2", "Yes", threshold=50.0)
    implication = deterministic_relation(strict, broad, 0.95)
    assert implication is not None
    assert implication["edge_type"] == "implies"
    assert implication["src_node_id"] == "strict"


def test_generated_rule_cases_gate_unsafe_rules() -> None:
    result = qualify_rule_registry(RULE_REGISTRY, deterministic_relation)
    assert {
        "same_market.binary_complement.v1",
        "same_market.categorical_exclusion.v1",
        "equivalence.normalized_fields.v1",
        "threshold.interval_containment.v2",
        "time.interval_containment.v1",
        "tournament.stage_progression.v1",
        "event.single_winner.v1",
    } <= set(result["enabled"])
    assert result["experimental"] == []
    assert result["support"]["event.single_winner.v1"]["adversarial_passed"] == 10


def test_solver_rejects_incompatible_soft_proposal_and_preserves_hard_fact() -> None:
    proposals = [
        _proposal("hard", "a", "b", "complement", 1.0, deterministic=True),
        _proposal("soft", "a", "b", "compatible", 0.999),
    ]
    accepted, rejected, stats = solve_proposals(proposals)
    assert [row["proposal_id"] for row in accepted] == ["hard"]
    assert [row["proposal_id"] for row in rejected] == ["soft"]
    assert rejected[0]["conflicting_constraint_ids"]
    assert stats["accepted"] == 1


def _source(text: str, index: int = 0) -> SourceMarket:
    return SourceMarket(
        market_id=f"m-{index}-{text}",
        question=text,
        description=text,
        source_hash=f"{index:064x}"[-64:],
        outcomes=(SourceOutcome(0, "Yes", f"t-{index}-y"), SourceOutcome(1, "No", f"t-{index}-n")),
    )


def _qualification_markets() -> list[SourceMarket]:
    phrases = {
        "sports": "Will the NBA team win?",
        "elections": "Will the candidate win the election?",
        "cryptocurrency": "Will Bitcoin exceed the target?",
        "economic_indicators": "Will CPI inflation fall?",
        "date_based": "Will the event happen by July?",
    }
    return [
        _source(phrase, domain_index * 1_000 + index)
        for domain_index, phrase in enumerate(phrases.values())
        for index in range(200)
    ]


def _proposition(
    identifier: str,
    market: str,
    outcome: str,
    *,
    polarity: str = "positive",
    threshold: float | None = None,
) -> dict[str, Any]:
    return {
        "proposition_id": identifier,
        "market_id": market,
        "event_id": "event",
        "event_slug": "event",
        "outcome": outcome,
        "_expected_tokens": 2,
        "subject": ["Bitcoin"],
        "predicate": "price",
        "object": None,
        "operator": "greater_than" if threshold is not None else None,
        "threshold": threshold,
        "unit": "USD" if threshold is not None else None,
        "time_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "time_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "competition": None,
        "event_scope": "event",
        "jurisdiction": None,
        "polarity": polarity,
        "parse_confidence": 0.99,
    }


def _proposal(
    identifier: str,
    src: str,
    dst: str,
    relation: str,
    confidence: float,
    *,
    deterministic: bool = False,
) -> dict[str, Any]:
    return {
        "proposal_id": identifier,
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_type": relation,
        "edge_basis": "same_market" if deterministic else "classifier",
        "confidence": confidence,
        "discovery_method": "deterministic" if deterministic else "generative_consensus",
        "rule_id": "same_market.binary_complement.v1" if deterministic else None,
        "rule_version": "v",
        "prompt_version": None,
    }
