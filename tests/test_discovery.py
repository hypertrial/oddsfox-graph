from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oddsfox_graph.benchmark import benchmark_summary
from oddsfox_graph.cli import main
from oddsfox_graph.discovery import (
    CLASSIFY_PROMPT_VERSION,
    DISCOVERY_PARQUET_ARTIFACTS,
    DiscoveryConfig,
    JsonCache,
    PairClassification,
    PairClassificationBatch,
    ParsedMarket,
    ParsedMarketBatch,
    ParsedOutcome,
    PropositionRecord,
    _candidate_sort_key,
    _canonical_entity,
    _canonical_unit,
    _deterministic_relation,
    _datetime_or_none,
    _generate_candidates,
    _is_winner_proposition,
    _load_source_markets,
    _validate_logic_edges,
    _validate_returned_ids,
    _with_retries,
    discover,
)
from oddsfox_graph.queries import DuckDB, q
from oddsfox_graph.review import REVIEW_FIELDS, export_review, score_review


REAL_INPUT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "polymarket_all_markets_20260730T093857Z.parquet"
)


class _Usage:
    input_tokens = 20
    output_tokens = 10
    total_tokens = 30


class _Response:
    def __init__(self, parsed: object) -> None:
        self.output_parsed = parsed
        self.model = "fake-model-2026-07-30"
        self.usage = _Usage()


class _FakeResponses:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, **kwargs: object) -> _Response:
        self.calls += 1
        payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
        if kwargs["text_format"] is ParsedMarketBatch:
            return _Response(_parse_markets(payload))
        pairs = []
        for index, item in enumerate(payload):
            pairs.append(
                PairClassification(
                    pair_id=item["pair_id"],
                    relation="uncertain" if index == 0 else "unrelated",
                    confidence=0.5 if index == 0 else 0.99,
                    explanation=(
                        "needs human context"
                        if index == 0
                        else "the propositions are unrelated"
                    ),
                    assumptions=[],
                    requires_review=index == 0,
                )
            )
        return _Response(PairClassificationBatch(pairs=pairs))


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def _parse_markets(payload: list[dict[str, Any]]) -> ParsedMarketBatch:
    markets = []
    for market in payload:
        propositions = []
        for source_outcome in market["outcomes"]:
            outcome = source_outcome["outcome"]
            if market["market_id"] in {"m150", "m100"}:
                threshold = 150_000.0 if market["market_id"] == "m150" else 100_000.0
                parsed = ParsedOutcome(
                    outcome=outcome,
                    subject=["Bitcoin" if market["market_id"] == "m150" else "BTC"],
                    predicate="price",
                    object=None,
                    operator="greater_than",
                    threshold=threshold,
                    unit="$",
                    time_start="2026-01-01T00:00:00Z",
                    time_end="2026-12-31T00:00:00Z",
                    competition=None,
                    jurisdiction=None,
                    polarity="negative" if outcome == "No" else "positive",
                    parse_confidence=0.99,
                )
            else:
                parsed = ParsedOutcome(
                    outcome=outcome,
                    subject=[market["market_id"]],
                    predicate="resolve",
                    object=None,
                    operator=None,
                    threshold=None,
                    unit=None,
                    time_start=None,
                    time_end=None,
                    competition=None,
                    jurisdiction=None,
                    polarity="negative" if outcome.casefold() == "no" else "positive",
                    parse_confidence=0.99,
                )
            propositions.append(parsed)
        markets.append(
            ParsedMarket(
                market_id=market["market_id"],
                propositions=propositions,
            )
        )
    return ParsedMarketBatch(markets=markets)


def _fake_embeddings(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
    matrix = np.zeros((len(texts), 4), dtype=np.float32)
    for index in range(len(texts)):
        matrix[index, index % 4] = 1.0
    return matrix


@pytest.fixture(scope="module")
def real_input() -> Path:
    if not REAL_INPUT.is_file():
        pytest.skip(f"real discovery input is unavailable: {REAL_INPUT}")
    return REAL_INPUT


@pytest.fixture(scope="module")
def real_discovery(
    tmp_path_factory: pytest.TempPathFactory,
    real_input: Path,
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("real-discovery")
    out = root / "out"
    cache = root / "cache"
    client = _FakeClient()
    config = DiscoveryConfig(
        cache_dir=cache,
        top_k=5,
        max_propositions=500,
        max_candidates=10_000,
        max_llm_pairs=100,
    )

    first_stats = discover(
        real_input,
        out,
        config=config,
        _client=client,
        _embedder=_fake_embeddings,
    )
    first_manifest = json.loads((out / "build_manifest.json").read_text())
    first_hashes = first_manifest["artifact_hashes"]
    calls = client.responses.calls

    offline = DiscoveryConfig(**{**config.__dict__, "offline": True})
    second_stats = discover(
        real_input,
        out,
        config=offline,
        _embedder=_fake_embeddings,
    )
    second_manifest = json.loads((out / "build_manifest.json").read_text())
    return {
        "out": out,
        "first_stats": first_stats,
        "first_manifest": first_manifest,
        "first_hashes": first_hashes,
        "calls": calls,
        "client": client,
        "second_stats": second_stats,
        "second_manifest": second_manifest,
    }


def test_discover_real_compact_input_and_offline_replay(
    real_discovery: dict[str, Any],
    real_input: Path,
) -> None:
    out = real_discovery["out"]
    first_stats = real_discovery["first_stats"]
    first_manifest = real_discovery["first_manifest"]
    first_hashes = real_discovery["first_hashes"]
    second_stats = real_discovery["second_stats"]
    second_manifest = real_discovery["second_manifest"]

    assert first_stats["tokens"] == 500
    assert first_stats["input_rows"] == 94_781
    selection = first_stats["input_selection"]
    assert selection["input_propositions"] == 189_578
    assert selection["invalid_market_rows"] == 4
    assert selection["selected_markets"] == 250
    assert selection["selected_propositions"] == 500
    assert selection["truncated"] is True
    assert set(DISCOVERY_PARQUET_ARTIFACTS) <= {
        path.name for path in out.glob("*.parquet")
    }
    assert (out / "graph_snapshot.json").is_file()
    assert (out / "reports" / "coverage.md").is_file()
    assert first_manifest["models"]["embedding"]["revision"]
    assert set(first_hashes) == set(DISCOVERY_PARQUET_ARTIFACTS)

    db = DuckDB()
    try:
        edges = {
            (row["src_node_id"], row["dst_node_id"], row["edge_type"])
            for row in db.rows(
                f"""
                SELECT src_node_id, dst_node_id, edge_type
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                """
            )
        }
        missing_from_source = int(
            db.scalar(
                f"""
                WITH source_ids AS (
                    SELECT unnest(clob_token_ids) AS proposition_id
                    FROM read_parquet('{q(real_input)}')
                )
                SELECT count(*)
                FROM read_parquet('{q(out / "propositions.parquet")}') p
                LEFT JOIN source_ids s USING (proposition_id)
                WHERE s.proposition_id IS NULL
                """
            )
            or 0
        )
        same_market_relations = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE edge_basis = 'same_market'
                """
            )
            or 0
        )
        selected_markets = db.rows(
            f"""
            SELECT market_id, count(*) AS propositions
            FROM read_parquet('{q(out / "propositions.parquet")}')
            GROUP BY market_id
            ORDER BY market_id
            LIMIT 3
            """
        )
        reviews = int(
            db.scalar(
                f"SELECT count(*) FROM read_parquet('{q(out / 'review_queue.parquet')}')"
            )
            or 0
        )
        candidate_invariants = db.rows(
            f"""
            SELECT
                count(*) - count(
                    DISTINCT proposition_a_id || '|' || proposition_b_id
                ) AS duplicate_pairs,
                max(embedding_rank) AS max_embedding_rank
            FROM read_parquet('{q(out / "relation_candidates.parquet")}')
            """
        )[0]
    finally:
        db.close()

    assert len(edges) >= 250
    assert same_market_relations == 250
    assert missing_from_source == 0
    assert all(row["propositions"] == 2 for row in selected_markets)
    assert candidate_invariants["duplicate_pairs"] == 0
    assert candidate_invariants["max_embedding_rank"] <= 5
    assert first_stats["candidate_edges"] <= 10_000
    assert first_stats["classified_pairs"] <= 100
    assert reviews >= 1
    summary = benchmark_summary(out)
    assert "classified_pairs:" in summary
    assert "review_queue:" in summary
    assert main(["search", "--out", str(out), "--query", "Will"]) == 0
    assert second_stats["logic_edges"] == first_stats["logic_edges"]
    assert second_manifest["artifact_hashes"] == first_hashes
    assert real_discovery["client"].responses.calls == real_discovery["calls"]
    assert second_manifest["cache"]["misses"] == 0


def test_discovery_auto_detects_real_data_reshaped_as_odds_export(
    tmp_path: Path,
    real_input: Path,
) -> None:
    input_path = tmp_path / "odds.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                WITH source AS (
                    SELECT *
                    FROM read_parquet('{q(real_input)}')
                    WHERE clob_token_ids IS NOT NULL
                    ORDER BY volume DESC NULLS LAST, market_id
                    LIMIT 1
                ),
                outcomes AS (
                    SELECT
                        source.*,
                        u.outcome,
                        u.ordinality - 1 AS outcome_index,
                        clob_token_ids[u.ordinality] AS clob_token_id
                    FROM source,
                    unnest(outcomes) WITH ORDINALITY AS u(outcome, ordinality)
                )
                SELECT
                    market_id,
                    outcome_index,
                    clob_token_id,
                    question,
                    outcome AS outcome_label,
                    event_slug,
                    true AS is_active,
                    false AS is_closed,
                    volume AS market_volume_usd,
                    epoch_ms(1722384000000 + sample.i * 60000) AS odds_timestamp,
                    1722384000 + sample.i * 60 AS odds_timestamp_epoch,
                    28706400 + sample.i AS odds_minute_epoch,
                    0.5::DOUBLE AS price
                FROM outcomes
                CROSS JOIN range(2) AS sample(i)
            ) TO '{q(input_path)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    source_format, input_rows, markets, selection = _load_source_markets(input_path)

    assert source_format == "minutely"
    assert input_rows > len(markets)
    assert sum(len(market.outcomes) for market in markets) == 2
    assert selection["input_propositions"] == 2
    assert {outcome.outcome for outcome in markets[0].outcomes} == {"Yes", "No"}


def test_deterministic_relation_directions_and_consistency() -> None:
    base = _proposition()
    high = {
        **base,
        "proposition_id": "high",
        "market_id": "high-market",
        "threshold": 150_000.0,
    }
    low = {
        **base,
        "proposition_id": "low",
        "market_id": "low-market",
        "threshold": 100_000.0,
    }
    positive = _deterministic_relation(high, low, 0.95)
    assert positive and (
        positive["src_node_id"],
        positive["dst_node_id"],
    ) == ("high", "low")

    high_no = {**high, "proposition_id": "high-no", "polarity": "negative"}
    low_no = {**low, "proposition_id": "low-no", "polarity": "negative"}
    negative = _deterministic_relation(high_no, low_no, 0.95)
    assert negative and (
        negative["src_node_id"],
        negative["dst_node_id"],
    ) == ("low-no", "high-no")

    low_confidence = {**high, "parse_confidence": 0.94}
    assert _deterministic_relation(low_confidence, low, 0.95) is None

    equivalent = {
        **base,
        "proposition_id": "equivalent",
        "market_id": "other-market",
    }
    relation = _deterministic_relation(base, equivalent, 0.95)
    assert relation and relation["edge_type"] == "equivalent"

    deterministic_edge = _edge("equivalent", "a", "b", "deterministic")
    llm_edge = _edge("mutually_exclusive", "a", "b", "llm")
    accepted, reviews = _validate_logic_edges([deterministic_edge, llm_edge])
    assert accepted == [deterministic_edge]
    assert reviews[0]["review_kind"] == "consistency_conflict"

    with pytest.raises(RuntimeError, match="Conflicting deterministic"):
        _validate_logic_edges(
            [
                deterministic_edge,
                _edge("mutually_exclusive", "a", "b", "deterministic"),
            ]
        )

    complement = _edge("complement", "a", "b", "deterministic")
    exclusion = _edge("mutually_exclusive", "a", "b", "deterministic")
    accepted, reviews = _validate_logic_edges([exclusion, complement])
    assert accepted == [complement]
    assert reviews == []

    with pytest.raises(RuntimeError, match="Conflicting deterministic implications"):
        _validate_logic_edges(
            [
                _edge("implies", "a", "b", "deterministic"),
                _edge("implies", "b", "a", "deterministic"),
            ]
        )


def test_cache_key_changes_with_model_prompt_schema_and_payload(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path / "cache")
    args = ("parse", "model-a", "prompt-v1", "prompt-hash-a", "schema-a")
    base = cache.key(*args, {"x": 1})
    assert base != cache.key(
        "parse", "model-b", "prompt-v1", "prompt-hash-a", "schema-a", {"x": 1}
    )
    assert base != cache.key(
        "parse", "model-a", "prompt-v2", "prompt-hash-a", "schema-a", {"x": 1}
    )
    assert base != cache.key(
        "parse", "model-a", "prompt-v1", "prompt-hash-b", "schema-a", {"x": 1}
    )
    assert base != cache.key(
        "parse", "model-a", "prompt-v1", "prompt-hash-a", "schema-b", {"x": 1}
    )
    assert base != cache.key(*args, {"x": 2})


def test_alias_unit_and_datetime_normalization() -> None:
    assert _canonical_entity(" ＢＴＣ ") == "Bitcoin"
    assert _canonical_entity("Manchester Utd") == "Manchester Utd"
    assert _canonical_unit("US dollars") == "USD"
    assert _canonical_unit("%") == "percent"
    assert _datetime_or_none("2026-07-30T09:00:00Z") == datetime(
        2026, 7, 30, 9, tzinfo=timezone.utc
    )
    assert _datetime_or_none(datetime(2026, 7, 30, 9)) == datetime(
        2026, 7, 30, 9, tzinfo=timezone.utc
    )


def test_candidate_priority_and_structured_batch_ids() -> None:
    row = {
        "candidate_reasons": {"embedding_top_k"},
        "proposition_a_id": "a",
        "proposition_b_id": "b",
    }
    assert _candidate_sort_key({**row, "embedding_similarity": 0.0}) < (
        _candidate_sort_key({**row, "embedding_similarity": None})
    )
    _validate_returned_ids(["a", "b"], ["b", "a"], "test")
    with pytest.raises(ValueError, match="do not match"):
        _validate_returned_ids(["a", "b"], ["a", "a"], "test")
    propositions = [
        {**_proposition(), "proposition_id": proposition_id}
        for proposition_id in ("a", "b")
    ]
    with pytest.raises(ValueError, match="invalid matrix"):
        _generate_candidates(
            propositions,
            DiscoveryConfig(top_k=1),
            lambda texts, config: np.full((len(texts), 1), np.nan),
        )


def test_structured_output_models_require_nullable_fields() -> None:
    parsed_schema = ParsedOutcome.model_json_schema()
    assert set(parsed_schema["required"]) == set(parsed_schema["properties"])
    assert {"type": "null"} in parsed_schema["properties"]["object"]["anyOf"]

    classified_schema = PairClassification.model_json_schema()
    assert set(classified_schema["required"]) == set(classified_schema["properties"])

    proposition_schema = PropositionRecord.model_json_schema()
    assert set(proposition_schema["required"]) == set(
        proposition_schema["properties"]
    )
    assert {"type": "null"} in proposition_schema["properties"]["event_id"]["anyOf"]
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
            jurisdiction=None,
            polarity="positive",
            parse_confidence=0.99,
        )


def test_retries_only_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientError(Exception):
        status_code = 429

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    transient_calls = 0

    async def transient_then_success() -> str:
        nonlocal transient_calls
        transient_calls += 1
        if transient_calls < 3:
            raise TransientError("rate limited")
        return "ok"

    assert asyncio.run(_with_retries(transient_then_success)) == "ok"
    assert transient_calls == 3

    permanent_calls = 0

    async def permanent_failure() -> None:
        nonlocal permanent_calls
        permanent_calls += 1
        raise ValueError("malformed structured output")

    with pytest.raises(ValueError, match="malformed"):
        asyncio.run(_with_retries(permanent_failure))
    assert permanent_calls == 1


def test_refusal_is_cached_and_routed_to_review(
    tmp_path: Path,
    real_input: Path,
) -> None:
    class RefusalResponses(_FakeResponses):
        async def parse(self, **kwargs: object) -> _Response:
            self.calls += 1
            payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
            if kwargs["text_format"] is ParsedMarketBatch:
                return _Response(_parse_markets(payload))
            return _Response(None)

    client = _FakeClient()
    client.responses = RefusalResponses()
    out = tmp_path / "out"
    discover(
        real_input,
        out,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "cache",
            top_k=1,
            max_propositions=4,
            max_candidates=20,
            max_llm_pairs=10,
        ),
        _client=client,
        _embedder=_fake_embeddings,
    )
    db = DuckDB()
    try:
        review_errors = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "review_queue.parquet")}')
                WHERE review_kind = 'classification_error'
                """
            )
            or 0
        )
    finally:
        db.close()
    assert review_errors > 0


def test_positive_labels_below_threshold_or_flagged_for_review_are_not_edges(
    tmp_path: Path,
    real_input: Path,
) -> None:
    class ThresholdResponses(_FakeResponses):
        async def parse(self, **kwargs: object) -> _Response:
            self.calls += 1
            payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
            if kwargs["text_format"] is ParsedMarketBatch:
                return _Response(_parse_markets(payload))
            pairs = [
                PairClassification(
                    pair_id=item["pair_id"],
                    relation="compatible",
                    confidence=0.94 if index == 0 else 0.99,
                    explanation="positive relation requires review",
                    assumptions=[],
                    requires_review=index != 0,
                )
                for index, item in enumerate(payload)
            ]
            return _Response(PairClassificationBatch(pairs=pairs))

    client = _FakeClient()
    client.responses = ThresholdResponses()
    out = tmp_path / "out"
    discover(
        real_input,
        out,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "cache",
            top_k=1,
            max_propositions=4,
            max_candidates=20,
            max_llm_pairs=10,
        ),
        _client=client,
        _embedder=_fake_embeddings,
    )
    db = DuckDB()
    try:
        llm_edges = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE discovery_method = 'llm'
                """
            )
            or 0
        )
        review_kinds = {
            str(row["review_kind"])
            for row in db.rows(
                f"""
                SELECT DISTINCT review_kind
                FROM read_parquet('{q(out / "review_queue.parquet")}')
                """
            )
        }
    finally:
        db.close()
    assert llm_edges == 0
    assert {
        "classification_low_confidence",
        "classification_requires_review",
    } <= review_kinds


def test_compact_input_validation_and_offline_cache_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_input: Path,
) -> None:
    bad_input = tmp_path / "bad.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT
                    'bad' AS market_id,
                    'Bad market?' AS question,
                    ['Yes', 'No'] AS outcomes,
                    ['only-one-token'] AS clob_token_ids
            ) TO '{q(bad_input)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match="equal-length"):
        _load_source_markets(bad_input)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Offline discovery cache"):
        discover(
            real_input,
            tmp_path / "out",
            config=DiscoveryConfig(
                cache_dir=tmp_path / "empty-cache",
                offline=True,
                max_propositions=2,
            ),
            _embedder=_fake_embeddings,
        )


def test_time_stage_single_winner_rules_and_candidate_cap() -> None:
    broad = {
        **_proposition(),
        "proposition_id": "broad",
        "market_id": "broad-market",
    }
    narrow = {
        **broad,
        "proposition_id": "narrow",
        "market_id": "narrow-market",
        "time_start": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "time_end": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    time_relation = _deterministic_relation(narrow, broad, 0.95)
    assert time_relation and (
        time_relation["src_node_id"],
        time_relation["dst_node_id"],
    ) == ("narrow", "broad")

    final = {
        **_proposition(),
        "proposition_id": "final",
        "market_id": "final-market",
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
        "proposition_id": "semi",
        "market_id": "semi-market",
        "object": "semifinal",
    }
    stage_relation = _deterministic_relation(final, semifinal, 0.95)
    assert stage_relation and (
        stage_relation["src_node_id"],
        stage_relation["dst_node_id"],
    ) == ("final", "semi")

    alice = {
        **final,
        "proposition_id": "alice",
        "market_id": "alice-market",
        "subject": ["Alice"],
        "predicate": "win",
        "object": "winner",
        "event_id": "election",
    }
    bob = {
        **alice,
        "proposition_id": "bob",
        "market_id": "bob-market",
        "subject": ["Bob"],
    }
    winner_relation = _deterministic_relation(alice, bob, 0.95)
    assert winner_relation and winner_relation["edge_type"] == "mutually_exclusive"
    assert not _is_winner_proposition(
        {**alice, "predicate": "wind speed", "object": None}
    )

    categorical = [
        {
            **_proposition(),
            "proposition_id": f"choice-{index}",
            "market_id": "categorical",
            "outcome": f"Choice {index}",
            "_expected_tokens": 3,
            "parse_status": "parsed",
        }
        for index in range(3)
    ]
    with pytest.raises(ValueError, match="refusing to truncate proven relations"):
        _generate_candidates(
            categorical,
            DiscoveryConfig(
                top_k=1,
                max_propositions=3,
                max_candidates=2,
            ),
            _fake_embeddings,
        )


def test_review_export_and_score_quality_gates(
    tmp_path: Path,
    real_discovery: dict[str, Any],
) -> None:
    out = real_discovery["out"]
    labels = tmp_path / "review.csv"

    counts = export_review(out, labels, accepted=200, recall_pairs=200, seed=7)
    assert counts == {"accepted_edges": 200, "recall_pairs": 200, "rows": 400}
    repeated_labels = tmp_path / "review-repeated.csv"
    assert export_review(
        out,
        repeated_labels,
        accepted=200,
        recall_pairs=200,
        seed=7,
    ) == counts
    assert repeated_labels.read_bytes() == labels.read_bytes()

    with labels.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["sample_type"] == "accepted_edge":
            row["reviewer_correct"] = "true"
            row["reviewer_expected_relation"] = row["proposed_relation"]
        else:
            row["reviewer_expected_relation"] = (
                "equivalent" if row["was_candidate"] == "true" else "unrelated"
            )
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    result = score_review(out, labels)
    assert result["passed"] is True
    assert result["metrics"]["deterministic_precision"] == 1.0
    assert result["metrics"]["overall_precision"] == 1.0
    assert result["metrics"]["candidate_recall"] == 1.0
    assert (out / "evaluation.json").is_file()

    recall_row = next(row for row in rows if row["sample_type"] == "recall_audit")
    expected_relation = recall_row["reviewer_expected_relation"]
    recall_row["reviewer_expected_relation"] = "invalid-relation"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="unsupported relations"):
        score_review(out, labels)
    recall_row["reviewer_expected_relation"] = expected_relation

    for row in rows[:3]:
        assert row["sample_type"] == "accepted_edge"
        row["reviewer_correct"] = "false"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    assert (
        main(
            [
                "review-score",
                "--out",
                str(out),
                "--labels",
                str(labels),
            ]
        )
        == 1
    )


def _proposition() -> dict[str, Any]:
    return {
        "proposition_id": "base",
        "market_id": "m",
        "event_id": "event",
        "event_slug": "event",
        "subject": ["Bitcoin"],
        "predicate": "price",
        "object": None,
        "operator": "greater_than",
        "threshold": 100_000.0,
        "unit": "USD",
        "time_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "time_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "competition": None,
        "jurisdiction": None,
        "polarity": "positive",
        "parse_confidence": 0.99,
        "outcome": "Yes",
        "_expected_tokens": 2,
    }


def _edge(
    edge_type: str, src: str, dst: str, method: str
) -> dict[str, Any]:
    return {
        "src_node_id": src,
        "dst_node_id": dst,
        "edge_type": edge_type,
        "edge_basis": "test",
        "confidence": 0.99,
        "market_id_src": "m1",
        "market_id_dst": "m2",
        "event_slug_src": "e",
        "event_slug_dst": "e",
        "evidence": "test evidence",
        "discovery_method": method,
        "rule_version": "test-v1" if method == "deterministic" else None,
        "model_version": "fake-model" if method == "llm" else None,
        "prompt_version": CLASSIFY_PROMPT_VERSION if method == "llm" else None,
        "explanation": "test evidence",
        "assumptions": [],
    }
