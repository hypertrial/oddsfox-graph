from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    ParsedOutcome,
    PropositionRecord,
)
from oddsfox_graph._discovery.candidates import _top_k_indices
from oddsfox_graph._discovery.input import (
    datetime_or_none,
    load_source_markets,
)
from oddsfox_graph._discovery.solver import solve_proposals
from oddsfox_graph.discovery import (
    DiscoveryConfig,
    _canonical_entity,
    _canonical_unit,
    _classification_validation_error,
    _deterministic_relation,
    _generate_candidate_store,
    _is_winner_proposition,
    _validate_logic_edges,
)
from oddsfox_graph.evaluation import (
    BENCHMARK_COLUMNS,
    REVIEW_FIELDS,
    _prediction_metrics,
    assign_domain,
    compile_benchmark,
)
from oddsfox_graph.queries import DuckDB, q


REAL_INPUT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "polymarket_all_markets_20260730T093857Z.parquet"
)


def _candidate_rows(
    propositions: list[dict[str, Any]],
    config: DiscoveryConfig,
    embedder: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    store = _generate_candidate_store(
        propositions,
        config,
        embedder,
        **kwargs,
    )
    try:
        return store.rows(order_by="proposition_a_id, proposition_b_id")
    finally:
        store.close()


def test_domain_taxonomy_uses_word_boundaries_and_precedence() -> None:
    assert assign_domain("Will the Ethiopian prime minister resign?") == "elections"
    assert assign_domain("Will ETH exceed $5,000 by June?") == "cryptocurrency"
    assert assign_domain("Will CPI inflation fall below 2%?") == "economic_indicators"
    assert assign_domain("Will the Lakers win the NBA Finals?") == "sports"
    assert assign_domain("Will the agreement be signed by July?") == "date_based"
    assert assign_domain("Will the company announce a new product?") == "other"


def test_alias_unit_and_datetime_normalization() -> None:
    assert _canonical_entity(" ＢＴＣ ") == "Bitcoin"
    assert _canonical_entity("Manchester Utd") == "Manchester Utd"
    assert _canonical_unit("US dollars") == "USD"
    assert _canonical_unit("%") == "percent"
    assert datetime_or_none("2026-07-30T09:00:00Z") == datetime(
        2026,
        7,
        30,
        9,
        tzinfo=timezone.utc,
    )
    assert datetime_or_none(datetime(2026, 7, 30, 9)) == datetime(
        2026,
        7,
        30,
        9,
        tzinfo=timezone.utc,
    )


def test_structured_contracts_require_nullable_fields() -> None:
    parsed_schema = ParsedOutcome.model_json_schema()
    assert set(parsed_schema["required"]) == set(parsed_schema["properties"])
    assert {"type": "null"} in parsed_schema["properties"]["object"]["anyOf"]

    classified_schema = AtomicPairAssessment.model_json_schema()
    assert set(classified_schema["required"]) == set(
        classified_schema["properties"]
    )

    proposition_schema = PropositionRecord.model_json_schema()
    assert set(proposition_schema["required"]) == set(
        proposition_schema["properties"]
    )
    assert {"type": "null"} in proposition_schema["properties"]["event_id"][
        "anyOf"
    ]

    with pytest.raises(ValueError):
        ParsedOutcome(
            outcome="Yes",
            subject=[],
            predicate=None,
            object=None,
            operator=None,
            threshold=None,
            unit=None,
            time_start=None,
            time_end=None,
            competition=None,
            event_scope=None,
            jurisdiction=None,
            polarity="positive",
            parse_confidence=0.99,
        )


def test_numeric_interval_implication_handles_mixed_operators_and_polarity() -> None:
    strict = _proposition("strict", operator="greater_than", threshold=100.0)
    inclusive = _proposition(
        "inclusive",
        operator="greater_than_or_equal",
        threshold=100.0,
    )
    relation = _deterministic_relation(strict, inclusive, 0.95)
    assert relation is not None
    assert relation["edge_type"] == "implies"
    assert (relation["src_node_id"], relation["dst_node_id"]) == (
        "strict",
        "inclusive",
    )
    assert relation["rule_id"] == "threshold.interval_containment.v2"
    candidates = _candidate_rows(
        [strict, inclusive],
        DiscoveryConfig(top_k=1, max_candidates=10),
        lambda texts, _: np.eye(len(texts), dtype=np.float32),
    )
    assert len(candidates) == 1
    assert candidates[0]["deterministic_relation"] == "implies"

    no_low = _proposition(
        "no-low",
        operator="greater_than",
        threshold=100.0,
        polarity="negative",
    )
    no_high = _proposition(
        "no-high",
        operator="greater_than",
        threshold=150.0,
        polarity="negative",
    )
    negative = _deterministic_relation(no_low, no_high, 0.95)
    assert negative is not None
    assert (negative["src_node_id"], negative["dst_node_id"]) == (
        "no-low",
        "no-high",
    )

    below = _proposition("below", operator="less_than", threshold=200.0)
    assert _deterministic_relation(strict, below, 0.95) is None

    unrelated_event = {
        **inclusive,
        "proposition_id": "other-event",
        "event_id": "other-event",
        "event_slug": "other-event",
    }
    assert _deterministic_relation(strict, unrelated_event, 0.95) is None
    cross_event_duplicate = {
        **strict,
        "proposition_id": "cross-event-duplicate",
        "market_id": "cross-event-market",
        "event_id": "other-event",
        "event_slug": "other-event",
    }
    assert _deterministic_relation(strict, cross_event_duplicate, 0.95) is None


def test_time_stage_and_single_winner_rules_require_matching_scope() -> None:
    broad = _proposition("broad")
    narrow = {
        **broad,
        "proposition_id": "narrow",
        "market_id": "market-narrow",
        "time_start": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "time_end": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    time_relation = _deterministic_relation(narrow, broad, 0.95)
    assert time_relation is not None
    assert (
        time_relation["src_node_id"],
        time_relation["dst_node_id"],
    ) == ("narrow", "broad")

    final = {
        **_proposition("final"),
        "subject": ["Alpha"],
        "predicate": "reach",
        "object": "final",
        "operator": None,
        "threshold": None,
        "unit": None,
        "competition": "World Cup",
    }
    semifinal = {
        **final,
        "proposition_id": "semifinal",
        "market_id": "market-semifinal",
        "object": "semifinal",
    }
    stage_relation = _deterministic_relation(final, semifinal, 0.95)
    assert stage_relation is not None
    assert (
        stage_relation["src_node_id"],
        stage_relation["dst_node_id"],
    ) == ("final", "semifinal")

    alice = {
        **final,
        "proposition_id": "alice",
        "market_id": "market-alice",
        "subject": ["Alice"],
        "predicate": "win",
        "object": "winner",
        "event_id": "election",
        "event_slug": "election",
        "event_scope": "single_winner",
    }
    bob = {
        **alice,
        "proposition_id": "bob",
        "market_id": "market-bob",
        "subject": ["Bob"],
    }
    winner_relation = _deterministic_relation(alice, bob, 0.95)
    assert winner_relation is not None
    assert winner_relation["edge_type"] == "mutually_exclusive"
    assert (
        _deterministic_relation(
            {**alice, "event_scope": "multi_winner"},
            {**bob, "event_scope": "multi_winner"},
            0.95,
        )
        is None
    )
    assert not _is_winner_proposition(
        {**alice, "predicate": "wind speed", "object": None}
    )


def test_blockwise_embeddings_are_stable_and_reusable() -> None:
    propositions = [
        _proposition(str(index), market_id=f"m-{index}")
        for index in range(6)
    ]
    calls: list[list[str]] = []

    def embed(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
        calls.append(list(texts))
        return np.asarray(
            [
                [float(index + 1), float((index + 1) % 3 + 1)]
                for index in range(len(texts))
            ],
            dtype=np.float32,
        )

    state: list[dict[str, Any]] = []
    config = DiscoveryConfig(
        top_k=2,
        embedding_block_size=2,
        max_candidates=100,
    )
    first = _candidate_rows(
        propositions,
        config,
        embed,
        embedding_state_sink=state,
    )
    assert len(calls) == 1
    assert len(state) == len(propositions)
    assert max(
        int(row["embedding_rank"])
        for row in first
        if row["embedding_rank"] is not None
    ) <= 2
    baseline = {
        str(row["text_hash"]): list(row["embedding"]) for row in state
    }

    def unexpected(_: list[str], __: DiscoveryConfig) -> np.ndarray:
        raise AssertionError("unchanged vectors must be reused")

    replay_state: list[dict[str, Any]] = []
    replay = _candidate_rows(
        propositions,
        config,
        unexpected,
        baseline_embeddings=baseline,
        embedding_state_sink=replay_state,
    )
    assert [
        (row["proposition_a_id"], row["proposition_b_id"])
        for row in replay
    ] == [
        (row["proposition_a_id"], row["proposition_b_id"])
        for row in first
    ]
    assert all(row["reused"] for row in replay_state)


def test_bounded_candidate_store_applies_stable_global_cap() -> None:
    propositions = [
        {
            **_proposition(f"p-{index:02d}"),
            "predicate": f"predicate-{index:02d}",
            "object": f"object-{index:02d}",
        }
        for index in range(20)
    ]
    rows = _candidate_rows(
        propositions,
        DiscoveryConfig(top_k=1, max_candidates=25),
        lambda texts, _: np.eye(len(texts), dtype=np.float32),
    )

    pairs = [
        (row["proposition_a_id"], row["proposition_b_id"])
        for row in rows
    ]
    assert len(pairs) == 25
    assert len(set(pairs)) == 25
    assert all(a_id < b_id for a_id, b_id in pairs)
    assert pairs == sorted(pairs)


def test_candidate_cap_never_truncates_deterministic_proofs() -> None:
    propositions = [
        {
            **_proposition(f"choice-{index}", market_id="categorical"),
            "outcome": f"Choice {index}",
            "_expected_tokens": 3,
        }
        for index in range(3)
    ]

    with pytest.raises(ValueError, match="refusing to truncate proven relations"):
        _candidate_rows(
            propositions,
            DiscoveryConfig(top_k=1, max_candidates=2),
            lambda texts, _: np.eye(len(texts), dtype=np.float32),
        )


def test_candidate_store_rejects_invalid_embedding_matrix() -> None:
    propositions = [_proposition("a"), _proposition("b")]

    with pytest.raises(ValueError, match="invalid matrix"):
        _candidate_rows(
            propositions,
            DiscoveryConfig(top_k=1, max_candidates=10),
            lambda texts, _: np.full((len(texts), 1), np.nan),
        )


def test_linear_top_k_preserves_score_and_stable_id_ties() -> None:
    scores = np.asarray([0.5, 0.7, 0.7, 0.7, 0.1], dtype=np.float32)
    assert _top_k_indices(scores, 2, np).tolist() == [1, 2]


def test_classifier_direction_and_supporting_field_validation() -> None:
    a = _proposition("a")
    b = _proposition("b", threshold=50.0)
    valid = AtomicPairAssessment(
        pair_id="a|b",
        a_implies_b="yes",
        b_implies_a="no",
        can_both_be_true="yes",
        must_one_be_true="no",
        logically_related="yes",
        confidence=0.99,
        supporting_fields=[
            {
                "proposition": "A",
                "field": "threshold",
                "value": "100.0",
            }
        ],
        assumptions=[],
        unsupported_assumption=False,
        requires_review=False,
    )
    assert _classification_validation_error(valid, a, b) is None
    invalid = valid.model_copy(
        update={
            "can_both_be_true": "no",
        }
    )
    assert "contradicts" in str(
        _classification_validation_error(invalid, a, b)
    )
    unsupported = valid.model_copy(
        update={
            "supporting_fields": [],
            "assumptions": ["Both markets use the same resolution source"],
        }
    )
    assert "without supporting-field" in str(
        _classification_validation_error(unsupported, a, b)
    )
    uncited_positive = valid.model_copy(update={"supporting_fields": []})
    assert "require supporting-field" in str(
        _classification_validation_error(uncited_positive, a, b)
    )
def test_rc2_preserves_same_market_fact_and_records_rejection() -> None:
    complement = _edge(
        "complement",
        "a",
        "b",
        confidence=1.0,
        method="deterministic",
        basis="same_market",
    )
    exclusion = _edge(
        "mutually_exclusive",
        "a",
        "b",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    accepted, rejected, stats = solve_proposals([exclusion, complement])

    assert [(row["edge_type"], row["discovery_method"]) for row in accepted] == [
        ("complement", "deterministic")
    ]
    assert rejected[0]["edge_type"] == "mutually_exclusive"
    assert rejected[0]["conflicting_proposal_ids"] == [
        complement["proposal_id"]
    ]
    assert "pair.incompatible_relations" in rejected[0][
        "conflicting_constraint_ids"
    ]
    assert stats["components"] == 1
    assert accepted[0]["solver_component_id"]


def test_rc2_publishes_one_strongest_proposal_per_typed_pair() -> None:
    weaker = _edge(
        "compatible",
        "a",
        "b",
        confidence=0.98,
        method="generative_model",
        basis="generative_model_classifier",
    )
    weaker["proposal_id"] = "weaker"
    stronger = {
        **weaker,
        "confidence": 0.99,
        "proposal_id": "stronger",
    }

    accepted, rejected, _ = solve_proposals([weaker, stronger])

    assert [row["proposal_id"] for row in accepted] == ["stronger"]
    assert [row["proposal_id"] for row in rejected] == ["weaker"]
    assert rejected[0]["conflicting_proposal_ids"] == ["stronger"]
    assert "pair.single_strongest_relation" in rejected[0][
        "conflicting_constraint_ids"
    ]
    complement = _edge(
        "complement",
        "a",
        "b",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    exclusion = _edge(
        "mutually_exclusive",
        "a",
        "b",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    accepted, _, _ = solve_proposals([exclusion, complement])
    assert [row["edge_type"] for row in accepted] == ["complement"]


def test_rc2_enforces_equivalence_class_relation_consistency() -> None:
    equivalent = _edge(
        "equivalent",
        "a",
        "b",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    complement = _edge(
        "complement",
        "a",
        "c",
        confidence=0.98,
        method="generative_model",
        basis="generative_model_classifier",
    )
    compatible = _edge(
        "compatible",
        "b",
        "c",
        confidence=0.97,
        method="generative_model",
        basis="generative_model_classifier",
    )

    accepted, rejected, _ = solve_proposals(
        [compatible, equivalent, complement]
    )

    assert {row["proposal_id"] for row in accepted} == {
        equivalent["proposal_id"],
        complement["proposal_id"],
    }
    assert [row["proposal_id"] for row in rejected] == [
        compatible["proposal_id"]
    ]
    assert "equivalence.class_relation_consistency" in rejected[0][
        "conflicting_constraint_ids"
    ]

    weak_equivalence = {
        **equivalent,
        "confidence": 0.5,
        "proposal_id": "weak-equivalence",
    }
    accepted, rejected, stats = solve_proposals(
        [compatible, weak_equivalence, complement]
    )
    assert {row["proposal_id"] for row in accepted} == {
        compatible["proposal_id"],
        complement["proposal_id"],
    }
    assert [row["proposal_id"] for row in rejected] == ["weak-equivalence"]
    assert rejected[0]["conflicting_proposal_ids"] == [
        compatible["proposal_id"],
        complement["proposal_id"],
    ]
    assert stats["objective_cost"] > 0


def test_rc2_prevents_exclusion_inside_equivalence_class() -> None:
    a_b = _edge(
        "equivalent",
        "a",
        "b",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    b_c = _edge(
        "equivalent",
        "b",
        "c",
        confidence=0.99,
        method="generative_model",
        basis="generative_model_classifier",
    )
    a_c = _edge(
        "mutually_exclusive",
        "a",
        "c",
        confidence=0.97,
        method="generative_model",
        basis="generative_model_classifier",
    )

    accepted, rejected, _ = solve_proposals([a_b, b_c, a_c])

    assert {row["proposal_id"] for row in accepted} == {
        a_b["proposal_id"],
        b_c["proposal_id"],
    }
    assert [row["proposal_id"] for row in rejected] == [a_c["proposal_id"]]
    assert "equivalence.class_self_exclusion" in rejected[0][
        "conflicting_constraint_ids"
    ]


def test_method_specific_relation_recall_uses_all_positive_truth() -> None:
    rows = [
        {
            "expected": "complement",
            "predicted": "complement",
            "method": "deterministic",
            "confidence": 1.0,
            "correct": True,
        },
        {
            "expected": "equivalent",
            "predicted": "equivalent",
            "method": "generative_model",
            "confidence": 0.99,
            "correct": True,
        },
    ]

    deterministic = _prediction_metrics(rows, method="deterministic")

    assert deterministic["precision"] == 1.0
    assert deterministic["recall"] == 0.5


@pytest.mark.parametrize("method", ["generative_model", "nli"])
def test_model_provenance_methods_are_current(method: str) -> None:
    edge = _edge(
        "compatible",
        "a",
        "b",
        confidence=0.99,
        method=method,
        basis=f"{method}_test",
    )
    accepted, reviews = _validate_logic_edges([edge])
    assert reviews == []
    assert accepted[0]["discovery_method"] == method


def test_benchmark_compile_requires_adjudication_and_preserves_reviewers(
    tmp_path: Path,
) -> None:
    if not REAL_INPUT.is_file():
        pytest.skip("real catalog is unavailable")
    reviewer_a = tmp_path / "reviewer-a.csv"
    reviewer_b = tmp_path / "reviewer-b.csv"
    adjudication = tmp_path / "adjudication.csv"
    _, _, markets, _ = load_source_markets(
        REAL_INPUT,
        max_propositions=5_000,
    )
    market = next(item for item in markets if len(item.outcomes) == 2)
    rows_a = _small_review_rows(
        "reviewer-a",
        pair_relation="complement",
        market=market,
    )
    rows_b = _small_review_rows(
        "reviewer-b",
        pair_relation="unrelated",
        market=market,
    )
    rows_adjudication = _small_review_rows(
        "adjudicator",
        pair_relation="complement",
        market=market,
    )
    _write_review_csv(reviewer_a, rows_a)
    _write_review_csv(reviewer_b, rows_b)
    _write_review_csv(adjudication, rows_adjudication)
    output = tmp_path / "benchmark.parquet"
    sampling = tmp_path / "sampling_manifest.json"
    sampling.write_text(
        json.dumps(
            {
                "benchmark_version": "v0.7.0",
                "source_sha256": hashlib.sha256(
                    REAL_INPUT.read_bytes()
                ).hexdigest(),
                "records": [
                    {
                        "record_id": row["record_id"],
                        "record_type": row["record_type"],
                        "pair_source": (
                            "candidate"
                            if row["record_type"] == "pair"
                            else None
                        ),
                    }
                    for row in rows_a
                ],
            }
        )
    )

    result = compile_benchmark(
        REAL_INPUT,
        reviewer_a,
        reviewer_b,
        adjudication,
        sampling,
        output,
        min_parse_records=2,
        min_pair_records=1,
        enforce_balance=False,
    )
    assert result["disagreements"] == 1
    db = DuckDB()
    try:
        pair = db.rows(
            f"""
            SELECT * FROM read_parquet('{q(output)}')
            WHERE record_type = 'pair'
            """
        )[0]
        columns = [
            str(row["column_name"])
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(output)}')"
            )
        ]
    finally:
        db.close()
    assert columns == list(BENCHMARK_COLUMNS)
    assert pair["expected_relation"] == "complement"
    assert pair["reviewer_a_alias"] == "reviewer-a"
    assert pair["reviewer_b_alias"] == "reviewer-b"
    assert pair["disagreement"] is True
    assert pair["disagreement_fields"] == ["expected_relation"]


def _proposition(
    proposition_id: str,
    *,
    market_id: str | None = None,
    operator: str = "greater_than",
    threshold: float = 100.0,
    polarity: str = "positive",
) -> dict[str, Any]:
    return {
        "proposition_id": proposition_id,
        "market_id": market_id or f"market-{proposition_id}",
        "event_id": "event",
        "event_slug": "event",
        "outcome": "Yes",
        "question": "Will Bitcoin exceed the threshold?",
        "description": "Resolves Yes when Bitcoin exceeds the stated threshold.",
        "subject": ["Bitcoin"],
        "predicate": "price",
        "object": None,
        "operator": operator,
        "threshold": threshold,
        "unit": "USD",
        "time_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "time_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "competition": None,
        "event_scope": None,
        "jurisdiction": None,
        "polarity": polarity,
        "parse_confidence": 0.99,
        "parse_status": "parsed",
        "_expected_tokens": 2,
    }


def _edge(
    edge_type: str,
    src: str,
    dst: str,
    *,
    confidence: float,
    method: str,
    basis: str,
) -> dict[str, Any]:
    proposal_id = f"{method}-{edge_type}-{src}-{dst}"
    return {
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_type": edge_type,
        "edge_basis": basis,
        "confidence": confidence,
        "market_id_src": "m",
        "market_id_dst": "m",
        "event_slug_src": "e",
        "event_slug_dst": "e",
        "evidence": "evidence",
        "discovery_method": method,
        "rule_version": "rules-v2" if method == "deterministic" else None,
        "model_version": (
            "fake-model" if method in {"generative_model", "nli"} else None
        ),
        "prompt_version": (
            "prompt-v2" if method in {"generative_model", "nli"} else None
        ),
        "explanation": "evidence",
        "assumptions": [],
        "rule_id": (
            "same_market.binary_complement.v1"
            if method == "deterministic"
            else None
        ),
        "proposal_id": proposal_id,
        "solver_version": None,
        "constraint_version": None,
        "solver_component_id": None,
    }


def _small_review_rows(
    alias: str,
    *,
    pair_relation: str,
    market: Any,
) -> list[dict[str, str]]:
    base = {field: "" for field in REVIEW_FIELDS}
    outcomes = sorted(
        market.outcomes,
        key=lambda item: item.clob_token_id,
    )
    domain = assign_domain(
        market.question,
        market.description,
        market.event_slug or "",
        market.category or "",
        market.event_id or "",
        market.tags,
    )
    parse_rows = []
    for outcome in outcomes:
        proposition_id = outcome.clob_token_id
        polarity = (
            "negative"
            if outcome.outcome.casefold() == "no"
            else "positive"
        )
        parse_rows.append(
            {
                **base,
                "record_id": hashlib.sha256(
                    f"parse|{proposition_id}".encode()
                ).hexdigest(),
                "record_type": "parse",
                "reviewer_alias": alias,
                "domain": domain,
                "proposition_a_id": proposition_id,
                "question_a": market.question,
                "description_a": market.description,
                "outcome_a": outcome.outcome,
                "expected_subjects_json": json.dumps(["Bitcoin"]),
                "expected_predicate": "price",
                "expected_operator": "greater_than",
                "expected_threshold": "100",
                "expected_unit": "USD",
                "expected_polarity": polarity,
                "reviewer_notes": "Reviewed against the supplied resolution text.",
            }
        )
    a_id, b_id = (
        outcomes[0].clob_token_id,
        outcomes[1].clob_token_id,
    )
    pair = {
        **base,
        "record_id": hashlib.sha256(
            f"pair|{a_id}|{b_id}".encode()
        ).hexdigest(),
        "record_type": "pair",
        "reviewer_alias": alias,
        "domain": domain,
        "proposition_a_id": a_id,
        "proposition_b_id": b_id,
        "question_a": market.question,
        "description_a": market.description,
        "question_b": market.question,
        "description_b": market.description,
        "outcome_a": outcomes[0].outcome,
        "outcome_b": outcomes[1].outcome,
        "expected_relation": pair_relation,
        "unsupported_assumption": "false",
        "reviewer_notes": "Reviewed both outcomes and resolution conditions.",
    }
    return [*parse_rows, pair]


def _write_review_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
