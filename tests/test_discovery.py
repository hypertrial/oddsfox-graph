from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oddsfox_graph.benchmark import benchmark_summary
from oddsfox_graph.cli import main
from oddsfox_graph._discovery.bulk import create_and_fill
from oddsfox_graph._discovery.cache import CACHE_ENTRY_VERSION, cache_entry
import oddsfox_graph.discovery as discovery_module
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
from oddsfox_graph.evaluation import (
    BENCHMARK_COLUMNS,
    evaluate_build,
    export_benchmark_reviews,
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
                    supporting_fields=[],
                    explanation=(
                        "needs human context"
                        if index == 0
                        else "the propositions are unrelated"
                    ),
                    assumptions=[],
                    a_implies_b={
                        "supported": False,
                        "supporting_fields": [],
                        "assumptions": [],
                    },
                    b_implies_a={
                        "supported": False,
                        "supporting_fields": [],
                        "assumptions": [],
                    },
                    requires_review=index == 0,
                )
            )
        return _Response(PairClassificationBatch(pairs=pairs))


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


class _StableFakeResponses:
    async def parse(self, **kwargs: object) -> _Response:
        payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
        if kwargs["text_format"] is ParsedMarketBatch:
            return _Response(_parse_markets(payload))
        return _Response(
            PairClassificationBatch(
                pairs=[
                    PairClassification(
                        pair_id=item["pair_id"],
                        relation="unrelated",
                        confidence=0.99,
                        supporting_fields=[],
                        explanation="the propositions are unrelated",
                        assumptions=[],
                        a_implies_b={
                            "supported": False,
                            "supporting_fields": [],
                            "assumptions": [],
                        },
                        b_implies_a={
                            "supported": False,
                            "supporting_fields": [],
                            "assumptions": [],
                        },
                        requires_review=False,
                    )
                    for item in payload
                ]
            )
        )


class _StableFakeClient:
    def __init__(self) -> None:
        self.responses = _StableFakeResponses()


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
                    event_scope=None,
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
                    event_scope=None,
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
    assert first_manifest["version"] == "0.5.0"
    assert first_manifest["versions"]["candidate_state"] == "candidate-components-v2"
    assert first_manifest["rules"]["enabled"] == [
        "same_market.binary_complement.v1",
        "same_market.categorical_exclusion.v1",
    ]
    assert first_manifest["rules"]["experimental"]
    assert first_manifest["rules"]["allow_unbenchmarked_rules"] is False
    assert (out / "state" / "candidate_blocks.parquet").is_file()
    assert (out / "state" / "candidate_reason_rows.parquet").is_file()
    assert (out / "state" / "execution_plan.parquet").is_file()

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
        solver_state_totals = db.rows(
            f"""
            SELECT
                sum(hard_clause_count)::BIGINT AS hard_clauses,
                sum(soft_clause_count)::BIGINT AS soft_clauses,
                sum(objective_cost)::BIGINT AS objective_cost
            FROM read_parquet(
                '{q(out / "state" / "solver_components.parquet")}'
            )
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
    assert second_stats == second_manifest["stats"]
    assert second_manifest["solver"] == second_stats["solver"]
    assert second_manifest["rules"] == second_stats["rules"]
    assert solver_state_totals == {
        "hard_clauses": second_stats["solver"]["hard_clauses"],
        "soft_clauses": second_stats["solver"]["soft_clauses"],
        "objective_cost": second_stats["solver"]["objective_cost"],
    }
    assert (
        f"- `runtime_seconds`: {second_stats['runtime_seconds']}"
        in (out / "reports" / "summary.md").read_text(encoding="utf-8")
    )
    assert "publish_files" in second_manifest["stage_timings"]
    assert "hash_artifacts" in second_manifest["stage_timings"]
    assert first_manifest["usage"]["total_tokens"] > 0
    assert first_manifest["usage"]["cached_origin"]["total_tokens"] == 0
    assert second_manifest["usage"]["total_tokens"] == 0
    assert (
        second_manifest["usage"]["cached_origin"]
        == first_manifest["usage"]["accounted_total"]
    )


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


def test_compact_selection_preserves_greedy_complete_market_semantics(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "selection.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT *
                FROM (
                    VALUES
                        ('oversized', 'Oversized?', ['A','B','C','D','E','F'], ['o1','o2','o3','o4','o5','o6'], 200.0),
                        ('three', 'Three?', ['A','B','C'], ['t1','t2','t3'], 100.0),
                        ('alpha', 'Alpha?', ['Yes','No'], ['a1','a2'], 90.0),
                        ('beta', 'Beta?', ['Yes','No'], ['b1','b2'], 90.0),
                        ('null-volume', 'Null?', ['Yes'], ['n1'], NULL)
                ) AS source(market_id, question, outcomes, clob_token_ids, volume)
            ) TO '{q(input_path)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()

    source_format, _, markets, selection = _load_source_markets(
        input_path,
        max_propositions=5,
    )
    assert source_format == "market_snapshot"
    assert [market.market_id for market in markets] == ["alpha", "three"]
    assert selection["eligible_markets"] == 5
    assert selection["eligible_propositions"] == 14
    assert selection["selected_propositions"] == 5


def test_real_data_m4_max_proposition_and_candidate_envelope(
    tmp_path: Path,
    real_input: Path,
) -> None:
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    config = DiscoveryConfig(
        cache_dir=cache,
        top_k=20,
        max_propositions=2_000,
        max_candidates=40_000,
        max_llm_pairs=200,
    )
    first = discover(
        real_input,
        out,
        config=config,
        _client=_FakeClient(),
        _embedder=_fake_embeddings,
    )
    first_manifest = json.loads((out / "build_manifest.json").read_text())
    second = discover(
        real_input,
        out,
        config=DiscoveryConfig(**{**config.__dict__, "offline": True}),
        _embedder=_fake_embeddings,
    )
    second_manifest = json.loads((out / "build_manifest.json").read_text())

    assert first["tokens"] == 2_000
    assert first["input_selection"]["selected_markets"] == 1_000
    assert first["candidate_edges"] <= 40_000
    assert first["classified_pairs"] <= 200
    assert second["logic_edges"] == first["logic_edges"]
    assert second_manifest["artifact_hashes"] == first_manifest["artifact_hashes"]


def test_benchmark_export_is_balanced_blinded_and_uses_documented_files(
    tmp_path: Path,
    real_input: Path,
) -> None:
    out = tmp_path / "discovery-5000"
    discover(
        real_input,
        out,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "cache",
            top_k=20,
            max_propositions=5_000,
            max_candidates=400_000,
            max_llm_pairs=10,
        ),
        _client=_FakeClient(),
        _embedder=_fake_embeddings,
    )
    review_dir = tmp_path / "benchmark-review"
    counts = export_benchmark_reviews(
        real_input,
        out,
        review_dir,
        seed=20260730,
    )

    assert counts == {"parse_records": 500, "pair_records": 2_000}
    assert {
        path.name for path in review_dir.glob("*.csv")
    } == {
        "reviewer-a.csv",
        "reviewer-b.csv",
        "adjudication.csv",
    }
    sampling = json.loads(
        (review_dir / "sampling_manifest.json").read_text()
    )
    assert sampling["domains"] == {
        "cryptocurrency": 100,
        "date_based": 100,
        "economic_indicators": 100,
        "elections": 100,
        "sports": 100,
    }
    assert set(sampling["pair_sources"]) == {
        "candidate",
        "noncandidate",
        "semantic_near_miss",
        "structured_near_miss",
    }
    assert sampling["pair_sources"]["candidate"] >= 200
    with (review_dir / "reviewer-a.csv").open(
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2_500
    assert all(row["reviewer_alias"] == "reviewer-a" for row in rows)
    assert all(not row["reviewer_notes"] for row in rows)
    assert all(not row["expected_relation"] for row in rows)


def test_incremental_baseline_reuses_parses_and_embedding_vectors(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    baseline = real_discovery["out"]
    out = tmp_path / "incremental"

    def unexpected_embeddings(
        _: list[str],
        __: DiscoveryConfig,
    ) -> np.ndarray:
        raise AssertionError("unchanged embedding vectors must be reused")

    stats = discover(
        real_input,
        out,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "new-cache",
            incremental_from=baseline,
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=_FakeClient(),
        _embedder=unexpected_embeddings,
    )
    manifest = json.loads((out / "build_manifest.json").read_text())

    assert stats["incremental"]["markets_reused"] == 250
    assert stats["incremental"]["markets_changed"] == 0
    assert stats["incremental"]["baseline_parse_entries_seeded"] == 250
    assert stats["incremental"]["baseline_classification_entries_seeded"] == 100
    assert stats["incremental"]["embedding_vectors_reused"] == 500
    assert stats["incremental"]["embedding_vectors_recomputed"] == 0
    assert stats["incremental"]["candidate_components_reused"] == 500
    assert stats["incremental"]["candidate_components_recomputed"] == 0
    assert stats["incremental"]["candidate_generation_reused"] is True
    assert stats["solver"]["components_reused"] > 0
    assert stats["solver"]["components_recomputed"] == 0
    assert (
        manifest["artifact_hashes"]
        == real_discovery["second_manifest"]["artifact_hashes"]
    )


def test_threshold_only_incremental_reuses_candidates_and_classifications(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    client = _FakeClient()
    thresholds = dict(DiscoveryConfig().relation_thresholds)
    thresholds["compatible"] = 0.97

    stats = discover(
        real_input,
        tmp_path / "threshold-only",
        config=DiscoveryConfig(
            cache_dir=tmp_path / "threshold-cache",
            incremental_from=real_discovery["out"],
            relation_thresholds=thresholds,
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=client,
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("threshold-only changes must reuse candidates")
        ),
    )

    assert client.responses.calls == 0
    assert stats["incremental"]["candidate_generation_reused"] is True
    assert stats["incremental"]["baseline_classification_entries_seeded"] == 100
    assert "relation_thresholds" in stats["incremental"]["invalidation_reasons"]
    assert stats["solver"]["components_recomputed"] == 0


def test_classification_budget_change_matches_clean_build(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    incremental = tmp_path / "budget-incremental"
    clean = tmp_path / "budget-clean"
    common = {
        "top_k": 5,
        "max_propositions": 500,
        "max_candidates": 10_000,
        "max_llm_pairs": 50,
    }
    discover(
        real_input,
        incremental,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "budget-incremental-cache",
            incremental_from=real_discovery["out"],
            **common,
        ),
        _client=_FakeClient(),
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("classification budget changes must reuse candidates")
        ),
    )
    discover(
        real_input,
        clean,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "budget-clean-cache",
            **common,
        ),
        _client=_FakeClient(),
        _embedder=_fake_embeddings,
    )
    incremental_manifest = json.loads(
        (incremental / "build_manifest.json").read_text()
    )
    clean_manifest = json.loads((clean / "build_manifest.json").read_text())
    assert (
        incremental_manifest["artifact_hashes"]
        == clean_manifest["artifact_hashes"]
    )


def test_candidate_cap_change_invalidates_structured_contributions(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    incremental = tmp_path / "candidate-cap-incremental"
    clean = tmp_path / "candidate-cap-clean"
    common = {
        "classify_model": "candidate-cap-test-classifier",
        "top_k": 5,
        "max_propositions": 500,
        "max_candidates": 1_000,
        "max_llm_pairs": 50,
    }
    incremental_stats = discover(
        real_input,
        incremental,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "candidate-cap-incremental-cache",
            incremental_from=real_discovery["out"],
            **common,
        ),
        _client=_StableFakeClient(),
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("unchanged embedding vectors must be reused")
        ),
    )
    discover(
        real_input,
        clean,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "candidate-cap-clean-cache",
            **common,
        ),
        _client=_StableFakeClient(),
        _embedder=_fake_embeddings,
    )
    incremental_manifest = json.loads(
        (incremental / "build_manifest.json").read_text()
    )
    clean_manifest = json.loads((clean / "build_manifest.json").read_text())
    assert "max_candidates" in incremental_stats["incremental"][
        "invalidation_reasons"
    ]
    assert incremental_stats["incremental"]["candidate_blocks_reused"] == 0
    assert incremental_stats["incremental"]["candidate_blocks_recomputed"] > 0
    assert (
        incremental_manifest["artifact_hashes"]
        == clean_manifest["artifact_hashes"]
    )


def test_incompatible_solver_state_is_not_reported_as_reused(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    baseline = tmp_path / "solver-version-baseline"
    shutil.copytree(real_discovery["out"], baseline)
    solver_state = baseline / "state" / "solver_components.parquet"
    replacement = baseline / "state" / "solver-components-replacement.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT * REPLACE ('incompatible-solver' AS solver_version)
                FROM read_parquet('{q(solver_state)}')
            ) TO '{q(replacement)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    replacement.replace(solver_state)

    stats = discover(
        real_input,
        tmp_path / "solver-version-incremental",
        config=DiscoveryConfig(
            cache_dir=tmp_path / "solver-version-cache",
            incremental_from=baseline,
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=_FakeClient(),
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("candidate state must remain reusable")
        ),
    )

    assert stats["solver"]["components_reused"] == 0
    assert stats["solver"]["components_recomputed"] > 0
    solver_plan = stats["incremental"]["execution_plan"]["stage_counts"][
        "solver_components"
    ]
    assert solver_plan.get("reused", 0) == 0
    assert solver_plan["recomputed"] > 0


def test_classifier_change_invalidates_only_classification_cache(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    client = _FakeClient()

    stats = discover(
        real_input,
        tmp_path / "classifier-change",
        config=DiscoveryConfig(
            cache_dir=tmp_path / "classifier-change-cache",
            incremental_from=real_discovery["out"],
            classify_model="replacement-classifier",
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=client,
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("classifier changes must reuse candidates")
        ),
    )

    assert stats["incremental"]["candidate_generation_reused"] is True
    assert stats["incremental"]["baseline_parse_entries_seeded"] == 250
    assert stats["incremental"]["baseline_classification_entries_seeded"] == 0
    assert "classifier_model_prompt_or_schema" in stats["incremental"][
        "invalidation_reasons"
    ]
    assert client.responses.calls == 5


def test_normalization_change_does_not_reuse_stale_classifications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(
        discovery_module,
        "NORMALIZATION_VERSION",
        "normalization-review-regression",
    )

    stats = discover(
        real_input,
        tmp_path / "normalization-change",
        config=DiscoveryConfig(
            cache_dir=tmp_path / "normalization-change-cache",
            incremental_from=real_discovery["out"],
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=client,
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("unchanged embedding vectors must be reused")
        ),
    )

    assert stats["incremental"]["baseline_parse_entries_seeded"] == 250
    assert stats["incremental"]["baseline_classification_entries_seeded"] == 0
    assert "normalization_version" in stats["incremental"][
        "invalidation_reasons"
    ]
    assert client.responses.calls == 5


def test_benchmark_evaluation_is_staged_and_published(
    tmp_path: Path,
    real_input: Path,
    real_discovery: dict[str, Any],
) -> None:
    db = DuckDB()
    try:
        propositions = db.rows(
            f"""
            SELECT *
            FROM read_parquet('{q(real_discovery["out"] / "propositions.parquet")}')
            QUALIFY count(*) OVER (PARTITION BY market_id) = 2
            ORDER BY market_id, outcome_index
            LIMIT 2
            """
        )
    finally:
        db.close()
    assert len(propositions) == 2
    source_hash = discovery_module._sha256(real_input)
    benchmark_rows = []
    for proposition in propositions:
        truth = {
            "expected_subjects": list(proposition["subject"] or []),
            "expected_predicate": proposition["predicate"],
            "expected_object": proposition["object"],
            "expected_operator": proposition["operator"],
            "expected_threshold": proposition["threshold"],
            "expected_unit": proposition["unit"],
            "expected_time_start": proposition["time_start"],
            "expected_time_end": proposition["time_end"],
            "expected_competition": proposition["competition"],
            "expected_event_scope": proposition["event_scope"],
            "expected_jurisdiction": proposition["jurisdiction"],
            "expected_polarity": proposition["polarity"],
        }
        benchmark_rows.append(
            {
                **{name: None for name in BENCHMARK_COLUMNS},
                "benchmark_version": "v0.4.0",
                "schema_version": "benchmark-v1",
                "source_sha256": source_hash,
                "record_id": f"parse-{proposition['proposition_id']}",
                "record_type": "parse",
                "domain": "date_based",
                "proposition_a_id": proposition["proposition_id"],
                **truth,
                "reviewer_a_alias": "reviewer-a",
                "reviewer_a_label": json.dumps(truth, default=str, sort_keys=True),
                "reviewer_a_notes": "Test fixture truth copied from typed parse.",
                "reviewer_b_alias": "reviewer-b",
                "reviewer_b_label": json.dumps(truth, default=str, sort_keys=True),
                "reviewer_b_notes": "Test fixture truth copied from typed parse.",
                "disagreement": False,
                "disagreement_fields": [],
                "final_notes": "Reviewers agreed.",
            }
        )
    a_id, b_id = sorted(str(row["proposition_id"]) for row in propositions)
    benchmark_rows.append(
        {
            **{name: None for name in BENCHMARK_COLUMNS},
            "benchmark_version": "v0.4.0",
            "schema_version": "benchmark-v1",
            "source_sha256": source_hash,
            "record_id": f"pair-{a_id}-{b_id}",
            "record_type": "pair",
            "domain": "date_based",
            "proposition_a_id": a_id,
            "proposition_b_id": b_id,
            "expected_relation": "complement",
            "unsupported_assumption": False,
            "reviewer_a_alias": "reviewer-a",
            "reviewer_a_label": '{"expected_relation":"complement"}',
            "reviewer_a_notes": "Same-market Yes/No outcomes.",
            "reviewer_b_alias": "reviewer-b",
            "reviewer_b_label": '{"expected_relation":"complement"}',
            "reviewer_b_notes": "Same-market Yes/No outcomes.",
            "disagreement": False,
            "disagreement_fields": [],
            "final_notes": "Reviewers agreed.",
        }
    )
    benchmark = tmp_path / "benchmark.parquet"
    db = DuckDB()
    try:
        create_and_fill(db, "benchmark_v", BENCHMARK_COLUMNS, benchmark_rows)
        db.execute(
            f"""
            COPY (
                SELECT *
                FROM benchmark_v
                ORDER BY record_type, record_id
            ) TO '{q(benchmark)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()

    out = tmp_path / "evaluated"
    stats = discover(
        real_input,
        out,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "evaluation-cache",
            incremental_from=real_discovery["out"],
            benchmark_path=benchmark,
            top_k=5,
            max_propositions=500,
            max_candidates=10_000,
            max_llm_pairs=100,
        ),
        _client=_FakeClient(),
        _embedder=lambda *_: (_ for _ in ()).throw(
            AssertionError("evaluation should reuse unchanged candidates")
        ),
    )
    manifest = json.loads((out / "build_manifest.json").read_text())
    report = json.loads((out / "evaluation_report.json").read_text())

    assert stats["evaluation_exit_decision"] == "NEEDS_RELATION_MODEL_WORK"
    assert report["exit_decision"] == "NEEDS_RELATION_MODEL_WORK"
    assert report["benchmark"]["source_sha256"] == source_hash
    assert (out / "benchmark.parquet").is_file()
    assert "benchmark.parquet" in manifest["artifact_hashes"]
    assert manifest["artifacts"][-2:] == [
        "benchmark.parquet",
        "evaluation_report.json",
    ]
    gated_out = tmp_path / "evaluated-gated"
    with pytest.raises(RuntimeError, match="READY_TO_SCALE"):
        discover(
            real_input,
            gated_out,
            config=DiscoveryConfig(
                cache_dir=tmp_path / "evaluation-gated-cache",
                incremental_from=real_discovery["out"],
                benchmark_path=benchmark,
                require_ready=True,
                top_k=5,
                max_propositions=500,
                max_candidates=10_000,
                max_llm_pairs=100,
            ),
            _client=_FakeClient(),
            _embedder=lambda *_: (_ for _ in ()).throw(
                AssertionError("evaluation should reuse unchanged candidates")
            ),
        )
    assert (gated_out / "build_manifest.json").is_file()
    assert (gated_out / "evaluation_report.json").is_file()

    standalone = tmp_path / "standalone-evaluation"
    shutil.copytree(real_discovery["out"], standalone)
    standalone_result = evaluate_build(standalone, benchmark)
    standalone_manifest = json.loads(
        (standalone / "build_manifest.json").read_text()
    )
    assert standalone_result["exit_decision"] == "NEEDS_RELATION_MODEL_WORK"
    assert standalone_manifest["stats"]["evaluation_exit_decision"] == (
        "NEEDS_RELATION_MODEL_WORK"
    )
    assert {
        "benchmark.parquet",
        "evaluation_report.json",
    } <= set(standalone_manifest["artifacts"])
    assert standalone_manifest["artifact_hashes"]["benchmark.parquet"]
    assert standalone_manifest["evaluation"]["hash"]


def test_one_changed_real_market_matches_clean_rebuild(
    tmp_path: Path,
    real_input: Path,
) -> None:
    class StableResponses(_FakeResponses):
        async def parse(self, **kwargs: object) -> _Response:
            self.calls += 1
            payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
            if kwargs["text_format"] is ParsedMarketBatch:
                return _Response(_parse_markets(payload))
            return _Response(
                PairClassificationBatch(
                    pairs=[
                        PairClassification(
                            pair_id=item["pair_id"],
                            relation="unrelated",
                            confidence=0.99,
                            supporting_fields=[],
                            explanation="The propositions are unrelated.",
                            assumptions=[],
                            a_implies_b={
                                "supported": False,
                                "supporting_fields": [],
                                "assumptions": [],
                            },
                            b_implies_a={
                                "supported": False,
                                "supporting_fields": [],
                                "assumptions": [],
                            },
                            requires_review=False,
                        )
                        for item in payload
                    ]
                )
            )

    def stable_client() -> _FakeClient:
        client = _FakeClient()
        client.responses = StableResponses()
        return client

    def content_embeddings(
        texts: list[str],
        _: DiscoveryConfig,
    ) -> np.ndarray:
        return np.asarray(
            [
                [
                    int.from_bytes(
                        hashlib.sha256(text.encode()).digest()[offset : offset + 4],
                        "big",
                    )
                    / 2**32
                    for offset in (0, 4, 8, 12)
                ]
                for text in texts
            ],
            dtype=np.float32,
        )

    config_kwargs = {
        "top_k": 3,
        "max_propositions": 100,
        "max_candidates": 2_000,
        "max_llm_pairs": 20,
    }
    baseline = tmp_path / "baseline"
    discover(
        real_input,
        baseline,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "baseline-cache",
            **config_kwargs,
        ),
        _client=stable_client(),
        _embedder=content_embeddings,
    )
    db = DuckDB()
    try:
        changed_market = str(
            db.scalar(
                f"""
                SELECT market_id
                FROM read_parquet('{q(baseline / "propositions.parquet")}')
                ORDER BY market_id
                LIMIT 1
                """
            )
        )
        escaped_market = changed_market.replace("'", "''")
        changed_input = tmp_path / "changed.parquet"
        db.execute(
            f"""
            COPY (
                SELECT * REPLACE (
                    CASE
                        WHEN CAST(market_id AS VARCHAR) = '{escaped_market}'
                            THEN coalesce(description, '')
                                 || ' Incremental validation change.'
                        ELSE description
                    END AS description
                )
                FROM read_parquet('{q(real_input)}')
            ) TO '{q(changed_input)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()

    incremental = tmp_path / "incremental-changed"
    incremental_stats = discover(
        changed_input,
        incremental,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "incremental-cache",
            incremental_from=baseline,
            **config_kwargs,
        ),
        _client=stable_client(),
        _embedder=content_embeddings,
    )
    clean = tmp_path / "clean-changed"
    discover(
        changed_input,
        clean,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "clean-cache",
            **config_kwargs,
        ),
        _client=stable_client(),
        _embedder=content_embeddings,
    )
    incremental_manifest = json.loads(
        (incremental / "build_manifest.json").read_text()
    )
    clean_manifest = json.loads((clean / "build_manifest.json").read_text())

    assert incremental_stats["incremental"]["markets_changed"] == 1
    assert incremental_stats["incremental"]["markets_reused"] == 49
    assert (
        incremental_stats["incremental"]["embedding_vectors_recomputed"] == 2
    )
    assert incremental_stats["incremental"]["candidate_generation_reused"] is False
    assert incremental_stats["incremental"]["semantic_neighborhoods_reused"] > 0
    assert incremental_stats["incremental"][
        "semantic_neighborhoods_recomputed"
    ] < 100
    assert incremental_stats["incremental"]["candidate_components_reused"] > 0
    assert incremental_stats["incremental"]["candidate_blocks_reused"] > 0
    assert incremental_stats["incremental"]["candidate_blocks_recomputed"] == 0
    assert incremental_stats["incremental"]["affected_only_verified"] is True
    assert incremental_stats["solver"]["components_reused"] > 0
    assert (
        incremental_manifest["artifact_hashes"]
        == clean_manifest["artifact_hashes"]
    )

    removed_input = tmp_path / "removed.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT *
                FROM read_parquet('{q(real_input)}')
                WHERE CAST(market_id AS VARCHAR) != '{escaped_market}'
            ) TO '{q(removed_input)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    removed_incremental = tmp_path / "incremental-removed"
    removed_stats = discover(
        removed_input,
        removed_incremental,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "removed-incremental-cache",
            incremental_from=baseline,
            **config_kwargs,
        ),
        _client=stable_client(),
        _embedder=content_embeddings,
    )
    removed_clean = tmp_path / "clean-removed"
    discover(
        removed_input,
        removed_clean,
        config=DiscoveryConfig(
            cache_dir=tmp_path / "removed-clean-cache",
            **config_kwargs,
        ),
        _client=stable_client(),
        _embedder=content_embeddings,
    )
    removed_incremental_manifest = json.loads(
        (removed_incremental / "build_manifest.json").read_text()
    )
    removed_clean_manifest = json.loads(
        (removed_clean / "build_manifest.json").read_text()
    )

    assert removed_stats["incremental"]["markets_removed"] == 1
    assert removed_stats["incremental"]["markets_changed"] == 1
    assert removed_stats["incremental"]["markets_reused"] == 49
    assert removed_stats["incremental"]["semantic_neighborhoods_reused"] > 0
    assert removed_stats["incremental"]["candidate_components_reused"] > 0
    assert removed_stats["incremental"]["candidate_blocks_reused"] > 0
    assert removed_stats["incremental"]["candidate_blocks_removed"] > 0
    assert removed_stats["incremental"]["affected_only_verified"] is True
    assert removed_stats["solver"]["components_reused"] > 0
    assert (
        removed_incremental_manifest["artifact_hashes"]
        == removed_clean_manifest["artifact_hashes"]
    )


def test_pre_v050_incremental_state_is_rejected_with_clean_build_guidance(
    tmp_path: Path,
    real_input: Path,
) -> None:
    baseline = tmp_path / "v040"
    baseline.mkdir()
    (baseline / "build_manifest.json").write_text(
        json.dumps(
            {
                "command": "discover",
                "version": "0.4.0",
                "versions": {"candidate_state": "candidate-components-v1"},
            }
        )
    )
    with pytest.raises(ValueError, match="clean v0.5 discover build"):
        discover(
            real_input,
            tmp_path / "out",
            config=DiscoveryConfig(
                incremental_from=baseline,
                max_propositions=2,
            ),
            _client=_FakeClient(),
            _embedder=_fake_embeddings,
        )


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


def test_cache_migration_and_concurrent_atomic_writes(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path / "cache")
    key = "legacy"
    (cache.directory / f"{key}.json").write_text(
        json.dumps(
            {
                "parsed": None,
                "error": "old error",
                "observed_model": "old-model",
                "usage": {},
            }
        ),
        encoding="utf-8",
    )
    assert cache.get(key) is None
    replayed = cache.get(key, offline=True)
    assert replayed is not None
    assert replayed["version"] == CACHE_ENTRY_VERSION
    assert replayed["state"] == "transient_failure"
    assert replayed["usage_accounting"] == "legacy_unscoped"
    assert cache.stats()["legacy_hits"] == 1

    concurrent_key = "same-key"

    def write(index: int) -> None:
        cache.put(
            concurrent_key,
            cache_entry(
                task="parse",
                parsed={"index": index},
                error=None,
                observed_model="fake",
                usage={},
                usage_scope=None,
                state="success",
            ),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))
    result = cache.get(concurrent_key)
    assert result is not None
    assert result["state"] == "success"
    assert 0 <= result["parsed"]["index"] < 32
    assert not list(cache.directory.glob("*.tmp"))


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
            event_scope=None,
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


def test_transient_cache_entry_replays_offline_but_recovers_online(
    tmp_path: Path,
    real_input: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientError(Exception):
        status_code = 429

    class RecoveringResponses(_FakeResponses):
        fail_classification = True

        async def parse(self, **kwargs: object) -> _Response:
            self.calls += 1
            payload = json.loads(kwargs["input"][1]["content"])  # type: ignore[index]
            if kwargs["text_format"] is ParsedMarketBatch:
                return _Response(_parse_markets(payload))
            if self.fail_classification:
                raise TransientError("rate limited")
            return _Response(
                PairClassificationBatch(
                    pairs=[
                        PairClassification(
                            pair_id=item["pair_id"],
                            relation="unrelated",
                            confidence=0.99,
                            supporting_fields=[],
                            explanation="not related",
                            assumptions=[],
                            a_implies_b={
                                "supported": False,
                                "supporting_fields": [],
                                "assumptions": [],
                            },
                            b_implies_a={
                                "supported": False,
                                "supporting_fields": [],
                                "assumptions": [],
                            },
                            requires_review=False,
                        )
                        for item in payload
                    ]
                )
            )

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = _FakeClient()
    client.responses = RecoveringResponses()
    out = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    base = DiscoveryConfig(
        cache_dir=cache_dir,
        top_k=1,
        max_propositions=4,
        max_candidates=20,
        max_llm_pairs=10,
    )
    discover(
        real_input,
        out,
        config=base,
        _client=client,
        _embedder=_fake_embeddings,
    )
    first_calls = client.responses.calls
    first_errors = _review_count(out, "classification_error")
    assert first_errors > 0

    discover(
        real_input,
        out,
        config=DiscoveryConfig(**{**base.__dict__, "offline": True}),
        _embedder=_fake_embeddings,
    )
    assert _review_count(out, "classification_error") == first_errors
    assert client.responses.calls == first_calls

    client.responses.fail_classification = False
    recovered = discover(
        real_input,
        out,
        config=base,
        _client=client,
        _embedder=_fake_embeddings,
    )
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert client.responses.calls > first_calls
    assert _review_count(out, "classification_error") == 0
    assert manifest["cache"]["transient_retries"] > 0
    assert recovered == manifest["stats"]


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
    calls = client.responses.calls
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
    assert client.responses.calls == calls


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
                    supporting_fields=[
                        {
                            "proposition": "A",
                            "field": "question",
                            "value": item["proposition_A"]["question"],
                        }
                    ],
                    explanation="positive relation requires review",
                    assumptions=[],
                    a_implies_b={
                        "supported": False,
                        "supporting_fields": [],
                        "assumptions": [],
                    },
                    b_implies_a={
                        "supported": False,
                        "supporting_fields": [],
                        "assumptions": [],
                    },
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

    null_element_input = tmp_path / "null-element.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT
                    'bad-null' AS market_id,
                    'Bad null market?' AS question,
                    ['Yes', NULL]::VARCHAR[] AS outcomes,
                    ['token-yes', 'token-no']::VARCHAR[] AS clob_token_ids
            ) TO '{q(null_element_input)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match="non-empty values"):
        _load_source_markets(null_element_input)
    with pytest.raises(ValueError, match="malformed eligible"):
        _load_source_markets(null_element_input, max_propositions=2)

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
        "event_scope": "single_winner",
    }
    bob = {
        **alice,
        "proposition_id": "bob",
        "market_id": "bob-market",
        "subject": ["Bob"],
    }
    winner_relation = _deterministic_relation(alice, bob, 0.95)
    assert winner_relation and winner_relation["edge_type"] == "mutually_exclusive"
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


def test_candidate_generation_caps_adversarial_shared_groups() -> None:
    propositions = [
        {
            **_proposition(),
            "proposition_id": f"p-{index:04d}",
            "market_id": f"m-{index:04d}",
            "predicate": f"predicate-{index}",
            "object": f"object-{index}",
            "parse_status": "parsed",
        }
        for index in range(400)
    ]
    candidates = _generate_candidates(
        propositions,
        DiscoveryConfig(
            top_k=2,
            max_propositions=400,
            max_candidates=1_000,
        ),
        _fake_embeddings,
    )
    assert len(candidates) == 1_000
    assert len(
        {
            (row["proposition_a_id"], row["proposition_b_id"])
            for row in candidates
        }
    ) == 1_000
    assert all(
        row["proposition_a_id"] < row["proposition_b_id"]
        for row in candidates
    )


def test_experimental_rules_do_not_consume_deterministic_candidate_reservation() -> None:
    propositions = [
        {
            **_proposition(),
            "proposition_id": f"equivalent-{index:02d}",
            "market_id": f"market-{index:02d}",
            "parse_status": "parsed",
        }
        for index in range(20)
    ]
    candidates = _generate_candidates(
        propositions,
        DiscoveryConfig(
            top_k=1,
            max_candidates=100,
        ),
        _fake_embeddings,
        enabled_rule_ids={
            "same_market.binary_complement.v1",
            "same_market.categorical_exclusion.v1",
        },
    )

    assert len(candidates) == 100
    assert all(
        candidate["deterministic_relation"] is None
        for candidate in candidates
    )


def test_candidate_cap_preserves_reason_priority_and_stable_ids() -> None:
    propositions = [
        {
            **_proposition(),
            "proposition_id": proposition_id,
            "market_id": f"market-{proposition_id}",
            "object": f"object-{proposition_id}",
            "parse_status": "parsed",
        }
        for proposition_id in ("a", "b", "c", "d")
    ]
    candidates = _generate_candidates(
        propositions,
        DiscoveryConfig(
            top_k=1,
            max_propositions=4,
            max_candidates=3,
        ),
        _fake_embeddings,
    )
    assert [
        (row["proposition_a_id"], row["proposition_b_id"])
        for row in candidates
    ] == [("a", "b"), ("a", "c"), ("a", "d")]
    assert all(
        row["candidate_reasons"]
        == [
            "compatible_predicate",
            "compatible_unit",
            "embedding_top_k",
            "overlapping_dates",
            "shared_entity",
            "shared_event",
        ]
        for row in candidates
    )


def test_bulk_insert_preserves_discovery_value_types() -> None:
    db = DuckDB()
    try:
        create_and_fill(
            db,
            "typed_rows",
            {
                "name": "VARCHAR",
                "score": "DOUBLE",
                "tags": "VARCHAR[]",
                "observed_at": "TIMESTAMPTZ",
                "enabled": "BOOLEAN",
            },
            [
                {
                    "name": "alpha",
                    "score": None,
                    "tags": ["x", "y"],
                    "observed_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
                    "enabled": True,
                },
                {
                    "name": "beta",
                    "score": None,
                    "tags": [],
                    "observed_at": None,
                    "enabled": False,
                },
            ],
            chunk_size=1,
        )
        rows = db.rows("SELECT * FROM typed_rows ORDER BY name")
    finally:
        db.close()
    assert rows[0]["tags"] == ["x", "y"]
    assert rows[0]["observed_at"] == datetime(
        2026, 7, 30, tzinfo=timezone.utc
    )
    assert rows[1]["score"] is None
    assert rows[1]["tags"] == []


def test_manifest_failure_removes_completion_marker(
    tmp_path: Path,
    real_input: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "out"
    config = DiscoveryConfig(
        cache_dir=tmp_path / "cache",
        top_k=1,
        max_propositions=2,
        max_candidates=10,
        max_llm_pairs=1,
    )
    discover(
        real_input,
        out,
        config=config,
        _client=_FakeClient(),
        _embedder=_fake_embeddings,
    )
    assert (out / "build_manifest.json").is_file()

    def fail_manifest(*_: object) -> None:
        raise OSError("simulated manifest failure")

    monkeypatch.setattr(discovery_module, "_write_manifest_last", fail_manifest)
    with pytest.raises(OSError, match="simulated"):
        discover(
            real_input,
            out,
            config=config,
            _client=_FakeClient(),
            _embedder=_fake_embeddings,
        )
    assert not (out / "build_manifest.json").exists()
    assert (out / "propositions.parquet").is_file()


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
    assert len(
        {
            (row["proposition_a_id"], row["proposition_b_id"])
            for row in rows
        }
    ) == len(rows)
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


def _review_count(out: Path, kind: str) -> int:
    db = DuckDB()
    try:
        return int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "review_queue.parquet")}')
                WHERE review_kind = ?
                """,
                [kind],
            )
            or 0
        )
    finally:
        db.close()
