"""Deterministic, catalog-derived model qualification.

Qualification cases are generated from the supplied market catalog without
external semantic labels or model-authored truth. The resulting measurements establish runtime
conformance and performance on controlled logical transformations; they are
not independent estimates of real-world semantic accuracy.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ._discovery.contracts import DiscoveryConfig, PropositionRecord, SourceMarket
from ._discovery.protocol import PairRequest, market_request, pair_identifier
from ._discovery.provenance import canonical_json_sha256
from ._discovery.relations import RULE_REGISTRY, deterministic_relation
from ._discovery.retrieval import generate_candidate_workspace
from ._discovery.versions import (
    NORMALIZATION_VERSION,
    PARSE_PROMPT_VERSION,
    QUALIFICATION_CASE_SCHEMA_VERSION,
    QUALIFICATION_GENERATOR_VERSION,
    RETRIEVAL_VERSION,
    SOURCE_SCHEMA,
)

if TYPE_CHECKING:
    from ._discovery.inference import StructuredClient


def qualify_catalog(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig,
    _primary_client: StructuredClient | None = None,
    _verifier_client: StructuredClient | None = None,
    _embedder: Callable[[list[str], DiscoveryConfig], Any] | None = None,
) -> dict[str, Any]:
    """Run automated qualification using the production inference contracts."""
    from ._discovery.pipeline import qualify_only

    return qualify_only(
        input_path,
        out_dir,
        config=config,
        _primary_client=_primary_client,
        _verifier_client=_verifier_client,
        _embedder=_embedder,
    )


DOMAINS = (
    "sports",
    "elections",
    "cryptocurrency",
    "economic_indicators",
    "date_based",
)
PUBLISHABLE_RELATIONS = (
    "complement",
    "equivalent",
    "mutually_exclusive",
    "implies",
    "compatible",
)


class _PropositionFields(TypedDict):
    subject: list[str]
    predicate: str
    object: str | None
    competition: str | None
    event_scope: str | None
    jurisdiction: str | None
RELATION_PRECISION_TARGETS = {
    "complement": 0.995,
    "equivalent": 0.99,
    "mutually_exclusive": 0.99,
    "implies": 0.98,
    "compatible": 0.98,
}

QUALIFICATION_CASE_COLUMNS = {
    "schema_version": "VARCHAR",
    "generator_version": "VARCHAR",
    "case_id": "VARCHAR",
    "record_type": "VARCHAR",
    "partition": "VARCHAR",
    "domain": "VARCHAR",
    "expected_relation": "VARCHAR",
    "source_market_ids": "VARCHAR[]",
    "source_proposition_ids": "VARCHAR[]",
    "generator_id": "VARCHAR",
    "payload_json": "VARCHAR",
    "case_hash": "VARCHAR",
}

_DOMAIN_PATTERNS = {
    "elections": re.compile(
        r"\b(election|elected|president|presidential|primary|nominee|"
        r"vote share|electoral|prime minister)\b",
        re.I,
    ),
    "cryptocurrency": re.compile(
        r"\b(bitcoin|btc|ethereum|eth|crypto|solana|xrp|dogecoin|blockchain)\b",
        re.I,
    ),
    "economic_indicators": re.compile(
        r"\b(gdp|inflation|cpi|unemployment|interest rates?|federal reserve|"
        r"recession|jobs report|nonfarm|payrolls?)\b",
        re.I,
    ),
    "sports": re.compile(
        r"\b(nba|nfl|nhl|mlb|fifa|uefa|champions league|world cup|"
        r"super bowl|premier league|tournament|championship|playoffs?)\b",
        re.I,
    ),
    "date_based": re.compile(
        r"\b(before|after|between|by|during|until|on)\b|"
        r"\b20\d{2}\b|\b(january|february|march|april|may|june|july|"
        r"august|september|october|november|december)\b",
        re.I,
    ),
}


class QualificationPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    record_type: Literal["parse", "pair"]
    partition: Literal["selection", "validation"]
    expected_relation: str | None
    primary_structured_valid: bool
    verifier_structured_valid: bool
    id_coverage: bool
    authoritative_conflict: bool
    parse_agreed: bool | None
    field_agreement: dict[str, float] = Field(default_factory=dict)
    primary_relation: str | None
    verifier_relation: str | None
    consensus_relation: str | None
    consensus_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    citations_valid: bool
    assumptions_empty: bool
    nli_veto: bool
    retrieved: bool = True
    stability_sampled: bool = False
    stable_across_seeds: bool = True


@dataclass(frozen=True)
class QualificationEvaluation:
    thresholds: dict[str, float]
    metrics: dict[str, Any]
    gates: dict[str, bool]
    status: Literal["AUTOMATION_VALIDATED", "QUALIFICATION_FAILED"]


@dataclass(frozen=True)
class RuleQualificationCase:
    rule_id: str
    case_id: str
    proposition_a: dict[str, Any]
    proposition_b: dict[str, Any]
    expected_relation: str | None
    expected_src: str | None
    expected_dst: str | None


def assign_domain(market: SourceMarket) -> str:
    return assign_domain_fields(
        question=market.question,
        description=market.description,
        event_slug=market.event_slug,
        event_id=market.event_id,
        category=market.category,
        tags=market.tags,
    )


def assign_domain_fields(
    *,
    question: str,
    description: str,
    event_slug: str | None,
    event_id: str | None,
    category: str | None,
    tags: tuple[str, ...],
) -> str:
    """Assign the shared versioned primary domain from authoritative fields."""

    text = " ".join(
        (
            question,
            description,
            event_slug or "",
            event_id or "",
            category or "",
            " ".join(tags),
        )
    )
    for domain in (
        "elections",
        "cryptocurrency",
        "economic_indicators",
        "sports",
        "date_based",
    ):
        if _DOMAIN_PATTERNS[domain].search(text):
            return domain
    return "other"


def generate_qualification_cases(
    markets: Sequence[SourceMarket],
    *,
    parse_count: int = 1_000,
    pair_count: int = 5_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    if parse_count != 1_000 or pair_count != 5_000:
        raise ValueError("Release qualification requires 1,000 parse and 5,000 pair cases")
    by_domain: dict[str, list[SourceMarket]] = defaultdict(list)
    for market in markets:
        domain = assign_domain(market)
        if domain in DOMAINS:
            by_domain[domain].append(market)
    for domain in DOMAINS:
        by_domain[domain].sort(
            key=lambda market: canonical_json_sha256(
                {
                    "seed": seed,
                    "source_hash": market.source_hash,
                    "market_id": market.market_id,
                    "domain": domain,
                }
            )
        )
        if len(by_domain[domain]) < 200:
            raise ValueError(
                f"Automated qualification requires 200 {domain} markets; "
                f"found {len(by_domain[domain])}"
            )

    selection_markets = [
        market for domain in DOMAINS for market in by_domain[domain][:120]
    ]
    validation_markets = [
        market for domain in DOMAINS for market in by_domain[domain][120:200]
    ]
    rows: list[dict[str, Any]] = []
    for domain in DOMAINS:
        for index, market in enumerate(by_domain[domain][:200]):
            partition = "selection" if index < 120 else "validation"
            rows.append(
                _case_row(
                    record_type="parse",
                    partition=partition,
                    domain=domain,
                    expected_relation=None,
                    markets=(market,),
                    proposition_ids=tuple(
                        outcome.clob_token_id for outcome in market.outcomes
                    ),
                    generator_id="source_market.parse_coverage.v1",
                    payload=market_request(market).model_dump(mode="json"),
                )
            )

    relation_counts = {
        relation: (300, 200) for relation in PUBLISHABLE_RELATIONS
    }
    relation_counts.update({"unrelated": (750, 500), "uncertain": (750, 500)})
    for relation, (selection_count, validation_count) in relation_counts.items():
        rows.extend(
            _pair_cases(
                selection_markets,
                relation,
                selection_count,
                partition="selection",
            )
        )
        rows.extend(
            _pair_cases(
                validation_markets,
                relation,
                validation_count,
                partition="validation",
            )
        )
    if sum(row["record_type"] == "parse" for row in rows) != parse_count or (
        sum(row["record_type"] == "pair" for row in rows) != pair_count
    ):
        raise RuntimeError("Qualification generator produced an invalid case count")
    return sorted(rows, key=lambda row: str(row["case_id"]))


def qualification_case_set_hash(rows: Sequence[dict[str, Any]]) -> str:
    return canonical_json_sha256(
        [
            {key: row[key] for key in QUALIFICATION_CASE_COLUMNS}
            for row in sorted(rows, key=lambda item: str(item["case_id"]))
        ]
    )


def qualification_retrieval_fingerprint(config: DiscoveryConfig) -> str:
    return canonical_json_sha256(
        {
            "generator": QUALIFICATION_GENERATOR_VERSION,
            "normalization": NORMALIZATION_VERSION,
            "retrieval": RETRIEVAL_VERSION,
            "embedding_model": config.embedding_model,
            "embedding_revision": config.embedding_revision,
            "top_k": config.top_k,
            "embedding_block_size": config.embedding_block_size,
            "max_candidates": config.max_candidates,
        }
    )


def qualification_retrieved_case_ids(
    cases: Sequence[dict[str, Any]],
    config: DiscoveryConfig,
    embedder: Callable[[list[str], DiscoveryConfig], Any],
) -> frozenset[str]:
    """Run production retrieval and return the generated pair cases it covers."""
    propositions: dict[str, dict[str, Any]] = {}
    expected: list[tuple[str, str, str]] = []
    for case in cases:
        if case["record_type"] != "pair":
            continue
        payload = json.loads(str(case["payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError("Qualification pair payload must be an object")
        proposition_a = payload.get("proposition_A")
        proposition_b = payload.get("proposition_B")
        if not isinstance(proposition_a, dict) or not isinstance(proposition_b, dict):
            raise ValueError("Qualification pair payload is missing propositions")
        a_id = str(proposition_a["proposition_id"])
        b_id = str(proposition_b["proposition_id"])
        propositions[a_id] = {str(key): value for key, value in proposition_a.items()}
        propositions[b_id] = {str(key): value for key, value in proposition_b.items()}
        propositions[a_id]["_expected_tokens"] = 2
        propositions[b_id]["_expected_tokens"] = 2
        expected.append((str(case["case_id"]), a_id, b_id))
    rule_support = qualify_rule_registry(RULE_REGISTRY, deterministic_relation)
    workspace = generate_candidate_workspace(
        [propositions[key] for key in sorted(propositions)],
        config,
        embedder,
        enabled_rule_ids=set(rule_support["enabled"]),
    )
    try:
        return workspace.matching_pair_ids(expected)
    finally:
        workspace.close()


def qualify_rule_registry(
    registry: Mapping[str, Mapping[str, object]],
    evaluator: Callable[[dict[str, Any], dict[str, Any], float], dict[str, Any] | None],
) -> dict[str, Any]:
    enabled: list[str] = []
    experimental: list[str] = []
    support: dict[str, dict[str, object]] = {}
    for rule_id, metadata in sorted(registry.items()):
        if bool(metadata.get("hard_fact")):
            enabled.append(rule_id)
            support[rule_id] = {
                "source": "authoritative_market_contract",
                "positive": None,
                "adversarial": None,
                "passed": True,
            }
            continue
        cases = generate_rule_qualification_cases(rule_id)
        positive = [case for case in cases if case.expected_relation is not None]
        adversarial = [case for case in cases if case.expected_relation is None]
        positive_passed = sum(
            _rule_case_passes(case, evaluator(case.proposition_a, case.proposition_b, 0.95))
            for case in positive
        )
        adversarial_passed = sum(
            evaluator(case.proposition_a, case.proposition_b, 0.95) is None
            for case in adversarial
        )
        passed = (
            len(positive) >= 10
            and len(adversarial) >= 10
            and positive_passed == len(positive)
            and adversarial_passed == len(adversarial)
        )
        (enabled if passed else experimental).append(rule_id)
        support[rule_id] = {
            "source": "independent_generated_cases",
            "positive": len(positive),
            "positive_passed": positive_passed,
            "adversarial": len(adversarial),
            "adversarial_passed": adversarial_passed,
            "passed": passed,
        }
    return {
        "qualification_kind": "independent_generated_cases",
        "minimum_positive_examples": 10,
        "minimum_adversarial_examples": 10,
        "enabled": enabled,
        "experimental": experimental,
        "support": support,
    }


def generate_rule_qualification_cases(rule_id: str) -> list[RuleQualificationCase]:
    cases: list[RuleQualificationCase] = []
    for index in range(10):
        a = _rule_proposition(f"{rule_id}-p-{index}-a", index * 2)
        b = _rule_proposition(f"{rule_id}-p-{index}-b", index * 2 + 1)
        negative_a = _rule_proposition(f"{rule_id}-n-{index}-a", 100 + index * 2)
        negative_b = _rule_proposition(f"{rule_id}-n-{index}-b", 101 + index * 2)
        if rule_id == "equivalence.normalized_fields.v1":
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
            start = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
            a.update(time_start=start + timedelta(days=2), time_end=start + timedelta(days=8))
            b.update(time_start=start, time_end=start + timedelta(days=10))
            negative_a.update(time_start=start, time_end=start + timedelta(days=10))
            negative_b.update(time_start=start + timedelta(days=5), time_end=start + timedelta(days=15))
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
        else:
            raise ValueError(f"No independent qualification generator for {rule_id}")
        cases.append(
            RuleQualificationCase(
                rule_id,
                f"{rule_id}:positive:{index}",
                a,
                b,
                expected,
                str(a["proposition_id"]) if expected == "implies" else min(str(a["proposition_id"]), str(b["proposition_id"])),
                str(b["proposition_id"]) if expected == "implies" else max(str(a["proposition_id"]), str(b["proposition_id"])),
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


def evaluate_qualification(
    cases: Sequence[dict[str, Any]],
    predictions: Sequence[QualificationPrediction],
) -> QualificationEvaluation:
    case_by_id = {str(row["case_id"]): row for row in cases}
    prediction_by_id = {row.case_id: row for row in predictions}
    if set(case_by_id) != set(prediction_by_id):
        raise ValueError("Qualification predictions do not cover every case exactly once")
    parse_rows = [row for row in predictions if row.record_type == "parse"]
    pair_rows = [row for row in predictions if row.record_type == "pair"]
    thresholds = {
        relation: _select_threshold(
            [
                row
                for row in pair_rows
                if row.partition == "selection"
            ],
            relation,
            RELATION_PRECISION_TARGETS[relation],
        )
        for relation in PUBLISHABLE_RELATIONS
    }
    relation_metrics = {
        relation: _relation_metrics(
            [row for row in pair_rows if row.partition == "validation"],
            relation,
            thresholds[relation],
        )
        for relation in PUBLISHABLE_RELATIONS
    }
    primary_parse_validity = sum(row.primary_structured_valid for row in parse_rows) / max(
        1, len(parse_rows)
    )
    verifier_parse_validity = sum(row.verifier_structured_valid for row in parse_rows) / max(
        1, len(parse_rows)
    )
    primary_pair_validity = sum(row.primary_structured_valid for row in pair_rows) / max(
        1, len(pair_rows)
    )
    verifier_pair_validity = sum(row.verifier_structured_valid for row in pair_rows) / max(
        1, len(pair_rows)
    )
    parse_consensus = sum(bool(row.parse_agreed) for row in parse_rows) / max(
        1, len(parse_rows)
    )
    field_names = sorted(
        {name for row in parse_rows for name in row.field_agreement}
    )
    field_agreement = {
        name: sum(row.field_agreement.get(name, 0.0) for row in parse_rows)
        / max(1, len(parse_rows))
        for name in field_names
    }
    accepted = [
        row
        for row in pair_rows
        if row.partition == "validation"
        and _accepted_by_threshold(row, thresholds)
    ]
    accepted_ids = {row.case_id for row in accepted}
    correct = sum(
        _normalized_relation(row.consensus_relation)
        == _normalized_relation(row.expected_relation)
        for row in accepted
    )
    total_positive = sum(
        _normalized_relation(row.expected_relation) in PUBLISHABLE_RELATIONS
        for row in pair_rows
        if row.partition == "validation"
    )
    correct_validation = sum(
        row.partition == "validation"
        and _normalized_relation(row.consensus_relation)
        == _normalized_relation(row.expected_relation)
        and row.case_id in accepted_ids
        for row in pair_rows
    )
    overall_precision = correct / max(1, len(accepted))
    overall_recall = correct_validation / max(1, total_positive)
    support_gate = all(
        int(metrics["correct"]) >= 200 for metrics in relation_metrics.values()
    )
    relation_precision_gate = all(
        float(relation_metrics[name]["precision"])
        >= RELATION_PRECISION_TARGETS[name]
        for name in PUBLISHABLE_RELATIONS
    )
    relation_recall_gate = all(
        float(metrics["recall"]) >= 0.80 for metrics in relation_metrics.values()
    )
    positive_pair_rows = [
        row
        for row in pair_rows
        if _normalized_relation(row.expected_relation) in PUBLISHABLE_RELATIONS
    ]
    retrieval_recall = sum(row.retrieved for row in positive_pair_rows) / max(
        1, len(positive_pair_rows)
    )
    gates = {
        "primary_parse_structured_validity": primary_parse_validity >= 0.999,
        "verifier_parse_structured_validity": verifier_parse_validity >= 0.999,
        "primary_pair_structured_validity": primary_pair_validity >= 0.999,
        "verifier_pair_structured_validity": verifier_pair_validity >= 0.999,
        "id_coverage": all(row.id_coverage for row in predictions),
        "authoritative_fields": not any(row.authoritative_conflict for row in parse_rows),
        "parse_consensus_coverage": parse_consensus >= 0.95,
        "scalar_field_agreement": all(
            value >= 0.97 for name, value in field_agreement.items()
            if name not in {"threshold", "unit", "time_start", "time_end"}
        ),
        "numeric_date_agreement": all(
            field_agreement.get(name, 1.0) >= 0.99
            for name in ("threshold", "unit", "time_start", "time_end")
        ),
        "relation_precision": relation_precision_gate,
        "overall_precision": overall_precision >= 0.97,
        "relation_recall": relation_recall_gate,
        "overall_recall": overall_recall >= 0.85,
        "relation_support": support_gate,
        "citations": all(row.citations_valid for row in accepted),
        "assumptions": all(row.assumptions_empty for row in accepted),
        "retrieval_recall": retrieval_recall >= 0.98,
        "seed_stability": _seed_stability_gate(pair_rows),
    }
    status: Literal["AUTOMATION_VALIDATED", "QUALIFICATION_FAILED"] = (
        "AUTOMATION_VALIDATED" if all(gates.values()) else "QUALIFICATION_FAILED"
    )
    return QualificationEvaluation(
        thresholds=thresholds,
        metrics={
            "primary_structured_validity": min(
                primary_parse_validity, primary_pair_validity
            ),
            "verifier_structured_validity": min(
                verifier_parse_validity, verifier_pair_validity
            ),
            "structured_output_validity": {
                "primary_parse": primary_parse_validity,
                "verifier_parse": verifier_parse_validity,
                "primary_pair": primary_pair_validity,
                "verifier_pair": verifier_pair_validity,
            },
            "parse_consensus_coverage": parse_consensus,
            "field_agreement": field_agreement,
            "relations": relation_metrics,
            "overall_precision": overall_precision,
            "overall_recall": overall_recall,
            "retrieval_recall": retrieval_recall,
            "semantic_accuracy_claim": False,
        },
        gates=gates,
        status=status,
    )


def _pair_cases(
    markets: Sequence[SourceMarket],
    relation: str,
    count: int,
    *,
    partition: Literal["selection", "validation"],
) -> list[dict[str, Any]]:
    if not markets:
        raise ValueError("Qualification pair generation requires source markets")
    rows = []
    for index in range(count):
        first = markets[index % len(markets)]
        second = markets[(index * 17 + 1) % len(markets)]
        payload, proposition_ids, generator_id = _pair_payload(
            first,
            second,
            relation,
            index,
        )
        rows.append(
            _case_row(
                record_type="pair",
                partition=partition,
                domain=assign_domain(first),
                expected_relation=relation,
                markets=(first, second),
                proposition_ids=proposition_ids,
                generator_id=generator_id,
                payload=payload,
            )
        )
    return rows


def _pair_payload(
    first: SourceMarket,
    second: SourceMarket,
    relation: str,
    index: int,
) -> tuple[dict[str, Any], tuple[str, str], str]:
    case_index = f"{relation}-{index}"
    scope = first.event_slug or first.event_id or f"scope-{first.market_id}"
    base_time = first.time_start or datetime(2030, 1, 1, tzinfo=timezone.utc)
    common: _PropositionFields = {
        "subject": [first.question],
        "predicate": "occur",
        "object": None,
        "competition": first.category,
        "event_scope": scope,
        "jurisdiction": None,
    }
    if relation == "complement":
        a = _qualification_proposition(first, case_index, "A", polarity="positive", **common)
        b = _qualification_proposition(first, case_index, "B", polarity="negative", **common)
        generator = "same_market.binary_complement.oracle.v1"
    elif relation == "equivalent":
        a = _qualification_proposition(first, case_index, "A", polarity="positive", **common)
        b = _qualification_proposition(first, case_index, "B", polarity="positive", **common)
        generator = "controlled.normalized_equivalence.oracle.v1"
    elif relation == "mutually_exclusive":
        first_fields: _PropositionFields = {**common, "subject": ["candidate A"]}
        second_fields: _PropositionFields = {**common, "subject": ["candidate B"]}
        a = _qualification_proposition(
            first, case_index, "A", polarity="positive", **first_fields
        )
        b = _qualification_proposition(
            first, case_index, "B", polarity="positive", **second_fields
        )
        generator = "same_scope.single_winner.oracle.v1"
    elif relation == "implies":
        if index % 3 == 0:
            a = _qualification_proposition(
                first,
                case_index,
                "A",
                polarity="positive",
                operator="greater_than",
                threshold=100.0 + index,
                unit="USD",
                **common,
            )
            b = _qualification_proposition(
                first,
                case_index,
                "B",
                polarity="positive",
                operator="greater_than",
                threshold=50.0 + index,
                unit="USD",
                **common,
            )
            generator = "numeric.interval_containment.oracle.v1"
        elif index % 3 == 1:
            a = _qualification_proposition(
                first,
                case_index,
                "A",
                polarity="positive",
                time_start=base_time + timedelta(days=2),
                time_end=base_time + timedelta(days=8),
                **common,
            )
            b = _qualification_proposition(
                first,
                case_index,
                "B",
                polarity="positive",
                time_start=base_time,
                time_end=base_time + timedelta(days=10),
                **common,
            )
            generator = "time.interval_containment.oracle.v1"
        else:
            winner_fields: _PropositionFields = {**common, "object": "winner"}
            finalist_fields: _PropositionFields = {**common, "object": "final"}
            a = _qualification_proposition(
                first,
                case_index,
                "A",
                polarity="positive",
                **winner_fields,
            )
            b = _qualification_proposition(
                first,
                case_index,
                "B",
                polarity="positive",
                **finalist_fields,
            )
            generator = "tournament.stage_progression.oracle.v1"
    elif relation == "compatible":
        if index % 2 == 0:
            a = _qualification_proposition(
                first,
                case_index,
                "A",
                polarity="positive",
                time_start=base_time,
                time_end=base_time + timedelta(days=10),
                **common,
            )
            b = _qualification_proposition(
                first,
                case_index,
                "B",
                polarity="positive",
                time_start=base_time + timedelta(days=5),
                time_end=base_time + timedelta(days=15),
                **common,
            )
            generator = "time.overlap_noncontainment.oracle.v1"
        else:
            a_fields: _PropositionFields = {**common, "subject": ["outcome A"]}
            b_fields: _PropositionFields = {**common, "subject": ["outcome B"]}
            a = _qualification_proposition(
                first, case_index, "A", polarity="positive", **a_fields
            )
            b = _qualification_proposition(
                first, case_index, "B", polarity="positive", **b_fields
            )
            generator = "same_scope.provably_co_possible.oracle.v1"
    elif relation == "unrelated":
        a = _qualification_proposition(first, case_index, "A", polarity="positive", **common)
        b = _qualification_proposition(
            second,
            case_index,
            "B",
            polarity="positive",
            subject=[second.question],
            predicate="occur",
            object=None,
            competition=second.category,
            event_scope=second.event_slug or second.event_id or f"scope-{second.market_id}",
            jurisdiction=None,
        )
        generator = "cross_scope.unrelated.oracle.v1"
    else:
        ambiguous: _PropositionFields = {**common, "event_scope": None}
        if index % 3 == 0:
            ambiguous["competition"] = None
            generator = "missing_scope.uncertain.oracle.v1"
        elif index % 3 == 1:
            ambiguous["object"] = "boundary unspecified"
            generator = "boundary_change.uncertain.oracle.v1"
        else:
            ambiguous["predicate"] = "resolve under unspecified criteria"
            generator = "underspecified_polarity.uncertain.oracle.v1"
        a = _qualification_proposition(first, case_index, "A", polarity="positive", **ambiguous)
        b = _qualification_proposition(second, case_index, "B", polarity="positive", **ambiguous)
    pair = PairRequest(
        pair_id=pair_identifier(a.proposition_id, b.proposition_id),
        proposition_A=a,
        proposition_B=b,
    )
    return (
        pair.model_dump(mode="json"),
        (a.proposition_id, b.proposition_id),
        generator,
    )


def _qualification_proposition(
    market: SourceMarket,
    index: str,
    suffix: str,
    *,
    subject: list[str],
    predicate: str,
    object: str | None,
    competition: str | None,
    event_scope: str | None,
    jurisdiction: str | None,
    polarity: Literal["positive", "negative"],
    operator: str | None = None,
    threshold: float | None = None,
    unit: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> PropositionRecord:
    proposition_id = f"qualification-{market.market_id}-{index}-{suffix}"
    return PropositionRecord.model_validate(
        {
            "proposition_id": proposition_id,
            "market_id": market.market_id,
            "event_id": market.event_id,
            "event_slug": market.event_slug,
            "clob_token_id": proposition_id,
            "outcome_index": 0 if suffix == "A" else 1,
            "outcome": suffix,
            "question": market.question,
            "description": market.description,
            "market_source_hash": market.source_hash,
            "normalization_version": NORMALIZATION_VERSION,
            "category": market.category,
            "tags": list(market.tags),
            "subject_original": subject,
            "subject": subject,
            "predicate": predicate,
            "object_original": object,
            "object": object,
            "operator": operator,
            "threshold": threshold,
            "unit_original": unit,
            "unit": unit,
            "time_start": time_start,
            "time_end": time_end,
            "competition_original": competition,
            "competition": competition,
            "event_scope_original": event_scope,
            "event_scope": event_scope,
            "jurisdiction_original": jurisdiction,
            "jurisdiction": jurisdiction,
            "polarity": polarity,
            "parse_confidence": 1.0,
            "parse_status": "parsed",
            "primary_parser_model": "qualification-oracle",
            "verifier_parser_model": "qualification-oracle",
            "prompt_version": PARSE_PROMPT_VERSION,
            "primary_parse_fingerprint": None,
            "verifier_parse_fingerprint": None,
            "consensus_fingerprint": None,
            "automation_profile_id": None,
            "source_schema": SOURCE_SCHEMA,
        }
    )


def _case_row(
    *,
    record_type: str,
    partition: str,
    domain: str,
    expected_relation: str | None,
    markets: Sequence[SourceMarket],
    proposition_ids: Sequence[str],
    generator_id: str,
    payload: object,
) -> dict[str, Any]:
    content = {
        "schema_version": QUALIFICATION_CASE_SCHEMA_VERSION,
        "generator_version": QUALIFICATION_GENERATOR_VERSION,
        "record_type": record_type,
        "partition": partition,
        "domain": domain,
        "expected_relation": expected_relation,
        "source_market_ids": [market.market_id for market in markets],
        "source_proposition_ids": list(proposition_ids),
        "generator_id": generator_id,
        "payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    }
    case_hash = canonical_json_sha256(content)
    return {
        **content,
        "case_id": case_hash,
        "case_hash": case_hash,
    }


def _select_threshold(
    rows: Sequence[QualificationPrediction],
    relation: str,
    target_precision: float,
) -> float:
    confidences = sorted(
        {
            float(row.consensus_confidence)
            for row in rows
            if _normalized_relation(row.consensus_relation) == relation
            and row.consensus_confidence is not None
        }
    )
    for threshold in confidences:
        metrics = _relation_metrics(rows, relation, threshold)
        if float(metrics["precision"]) >= target_precision and int(metrics["correct"]) >= 200:
            return threshold
    return 1.0


def _relation_metrics(
    rows: Sequence[QualificationPrediction],
    relation: str,
    threshold: float,
) -> dict[str, float | int]:
    predicted = [
        row
        for row in rows
        if _normalized_relation(row.consensus_relation) == relation
        and row.consensus_confidence is not None
        and row.consensus_confidence >= threshold
        and row.citations_valid
        and row.assumptions_empty
        and not row.nli_veto
    ]
    truth = [row for row in rows if _normalized_relation(row.expected_relation) == relation]
    correct = sum(
        _normalized_relation(row.expected_relation) == relation for row in predicted
    )
    return {
        "threshold": threshold,
        "support": len(predicted),
        "truth": len(truth),
        "correct": correct,
        "precision": correct / max(1, len(predicted)),
        "recall": correct / max(1, len(truth)),
    }


def _normalized_relation(value: str | None) -> str | None:
    if value in {"A_implies_B", "B_implies_A"}:
        return "implies"
    return value


def _accepted_by_threshold(
    row: QualificationPrediction,
    thresholds: dict[str, float],
) -> bool:
    relation = _normalized_relation(row.consensus_relation)
    return bool(
        relation in PUBLISHABLE_RELATIONS
        and row.consensus_confidence is not None
        and row.consensus_confidence >= thresholds[str(relation)]
        and row.citations_valid
        and row.assumptions_empty
        and not row.nli_veto
    )


def _seed_stability_gate(rows: Sequence[QualificationPrediction]) -> bool:
    sampled = [row for row in rows if row.stability_sampled]
    return bool(
        len(sampled) == 500
        and sum(row.stable_across_seeds for row in sampled) / len(sampled) >= 0.99
    )
