from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import oddsfox_graph.discovery as discovery_module
from oddsfox_graph._discovery.contracts import (
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
    ParsedOutcome,
)
from oddsfox_graph._discovery.input import load_source_markets
from oddsfox_graph._discovery.versions import SOURCE_SCHEMA
from oddsfox_graph.cli import main
from oddsfox_graph.discovery import DISCOVERY_PARQUET_ARTIFACTS, discover
from oddsfox_graph.queries import DuckDB, q


ROOT = Path(__file__).resolve().parents[1]
REAL_INPUT = ROOT / "data" / "polymarket_all_markets_20260730T093857Z.parquet"
REAL_INPUT_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)
requires_real_catalog = pytest.mark.skipif(
    not REAL_INPUT.is_file(),
    reason="canonical release catalog is an external fixture",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _Response:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.observed_model = "Qwen/Qwen3-4B-GGUF:Q8_0"
        self.usage = {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
        }


class _FakeClient:
    async def generate(self, **kwargs: object) -> _Response:
        payload = kwargs["payload"]
        if kwargs["response_model"] is ParsedMarket:
            assert isinstance(payload, dict)
            return _Response(
                ParsedMarket(
                    market_id=str(payload["market_id"]),
                    propositions=[
                        ParsedOutcome(
                            outcome=str(outcome["outcome"]),
                            subject=[str(payload["market_id"])],
                            predicate="resolve",
                            object=None,
                            operator=None,
                            threshold=None,
                            unit=None,
                            time_start=None,
                            time_end=None,
                            competition=None,
                            event_scope=str(payload.get("event_slug") or ""),
                            jurisdiction=None,
                            polarity=(
                                "negative"
                                if str(outcome["outcome"]).casefold() == "no"
                                else "positive"
                            ),
                            parse_confidence=0.99,
                        )
                        for outcome in payload["outcomes"]
                    ],
                )
            )
        assert isinstance(payload, dict)
        return _Response(
            AtomicPairAssessment(
                pair_id=str(payload["pair_id"]),
                a_implies_b="no",
                b_implies_a="no",
                can_both_be_true="yes",
                must_one_be_true="no",
                logically_related="yes",
                confidence=0.99,
                supporting_fields=[
                    {
                        "proposition": side,
                        "field": "question",
                        "value": str(payload[f"proposition_{side}"]["question"]),
                    }
                    for side in ("A", "B")
                ],
                assumptions=[],
                unsupported_assumption=False,
                requires_review=False,
            )
        )


def _embeddings(texts: list[str], _: DiscoveryConfig) -> np.ndarray:
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


def _write_catalog(path: Path) -> None:
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE catalog (
                event_id VARCHAR,
                event_slug VARCHAR,
                market_id VARCHAR,
                question VARCHAR,
                description VARCHAR,
                outcomes VARCHAR[],
                clob_token_ids VARCHAR[],
                volume DOUBLE,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                category VARCHAR,
                tags VARCHAR[]
            )
            """
        )
        db.execute(
            """
            INSERT INTO catalog VALUES
                ('e1', 'event-one', 'm1', 'Will Alpha happen?',
                 'Alpha resolution market.', ['Yes', 'No'], ['m1-y', 'm1-n'],
                 300.0, '2026-01-01', '2026-12-31', 'events', ['alpha']),
                ('e2', 'event-two', 'm2', 'Will Beta happen?',
                 'Beta resolution market.', ['Yes', 'No'], ['m2-y', 'm2-n'],
                 200.0, '2026-01-01', '2026-12-31', 'events', ['beta']),
                ('e3', 'event-three', 'm3', 'Which color wins?',
                 'One color wins.', ['Red', 'Blue', 'Green'],
                 ['m3-r', 'm3-b', 'm3-g'], 100.0, '2026-01-01',
                 '2026-12-31', 'events', ['color'])
            """
        )
        db.execute(
            f"COPY catalog TO '{q(path)}' (FORMAT PARQUET)"
        )
    finally:
        db.close()


@requires_real_catalog
def test_supplied_catalog_binding_and_schema() -> None:
    assert REAL_INPUT.is_file()
    assert _sha256(REAL_INPUT) == REAL_INPUT_SHA256
    db = DuckDB()
    try:
        assert db.scalar(
            f"SELECT count(*) FROM read_parquet('{q(REAL_INPUT)}')"
        ) == 94_781
        columns = {
            str(row["name"])
            for row in db.rows(
                f"SELECT name FROM parquet_schema('{q(REAL_INPUT)}')"
                " WHERE name != 'duckdb_schema'"
            )
        }
    finally:
        db.close()
    assert {
        "event_id",
        "event_slug",
        "market_id",
        "question",
        "description",
        "outcomes",
        "clob_token_ids",
        "volume",
        "start_time",
        "end_time",
        "category",
        "tags",
    } <= columns


def test_compact_loader_is_deterministic_and_rejects_old_export_shape(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    schema, input_rows, markets, selection = load_source_markets(
        catalog,
        max_propositions=5,
    )
    assert schema == SOURCE_SCHEMA
    assert input_rows == 3
    assert [market.market_id for market in markets] == ["m1", "m2"]
    assert selection["selected_propositions"] == 4

    old_export = tmp_path / "old-export.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT
                    'm'::VARCHAR AS market_id,
                    'Question'::VARCHAR AS question,
                    'Yes'::VARCHAR AS outcome_label,
                    'token'::VARCHAR AS clob_token_id,
                    'event'::VARCHAR AS event_slug,
                    1::BIGINT AS odds_timestamp_epoch
            ) TO '{q(old_export)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match=SOURCE_SCHEMA):
        load_source_markets(old_export)

    wrong_types = tmp_path / "wrong-types.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT
                    'm'::VARCHAR AS market_id,
                    'Question'::VARCHAR AS question,
                    'Yes,No'::VARCHAR AS outcomes,
                    'token-yes,token-no'::VARCHAR AS clob_token_ids
            ) TO '{q(wrong_types)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match="incompatible column types"):
        load_source_markets(wrong_types)

    wrong_tags = tmp_path / "wrong-tags.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT
                    'm'::VARCHAR AS market_id,
                    'Question'::VARCHAR AS question,
                    ['Yes', 'No']::VARCHAR[] AS outcomes,
                    ['token-yes', 'token-no']::VARCHAR[] AS clob_token_ids,
                    'tag'::VARCHAR AS tags
            ) TO '{q(wrong_tags)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match=r"tags must have type VARCHAR\[\]"):
        load_source_markets(wrong_tags)


@requires_real_catalog
def test_discovery_online_offline_and_query_interfaces(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    config = DiscoveryConfig(
        cache_dir=cache,
        top_k=2,
        max_propositions=6,
        max_candidates=100,
        max_llm_pairs=20,
    )
    first = discover(
        REAL_INPUT,
        out,
        config=config,
        _client=_FakeClient(),
        _embedder=_embeddings,
    )
    first_manifest = json.loads(
        (out / "build_manifest.json").read_text(encoding="utf-8")
    )
    first_hashes = dict(first_manifest["artifact_hashes"])

    second = discover(
        REAL_INPUT,
        out,
        config=DiscoveryConfig(**{**config.__dict__, "offline": True}),
        _embedder=_embeddings,
    )
    second_manifest = json.loads(
        (out / "build_manifest.json").read_text(encoding="utf-8")
    )
    incremental_out = tmp_path / "incremental"
    incremental = discover(
        REAL_INPUT,
        incremental_out,
        config=DiscoveryConfig(
            **{
                **config.__dict__,
                "incremental_from": out,
                "offline": True,
                "model_manifest": out / "model_manifest.json",
                "model_profile": out / "model_profile.json",
            }
        ),
        _embedder=_embeddings,
    )
    incremental_manifest = json.loads(
        (incremental_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    threshold_out = tmp_path / "threshold-only"
    tightened_thresholds = {
        **config.relation_thresholds,
        "complement": 0.999,
    }
    threshold_stats = discover(
        REAL_INPUT,
        threshold_out,
        config=DiscoveryConfig(
            **{
                **config.__dict__,
                "incremental_from": out,
                "offline": True,
                "model_manifest": out / "model_manifest.json",
                "model_profile": out / "model_profile.json",
                "relation_thresholds": tightened_thresholds,
            }
        ),
        _embedder=_embeddings,
    )
    threshold_manifest = json.loads(
        (threshold_out / "build_manifest.json").read_text(encoding="utf-8")
    )

    assert first["input_schema"] == SOURCE_SCHEMA
    assert first["tokens"] == 6
    assert first["input_rows"] == 94_781
    assert first_hashes == second_manifest["artifact_hashes"]
    assert first_hashes == incremental_manifest["artifact_hashes"]
    assert first_hashes == threshold_manifest["artifact_hashes"]
    assert second["incremental"]["offline_state_replay"] is True
    assert incremental["incremental"]["candidate_generation_reused"] is True
    assert threshold_stats["incremental"]["candidate_generation_reused"] is True
    assert "relation_thresholds" in (
        threshold_stats["incremental"]["invalidation_reasons"]
    )
    assert threshold_manifest["cache"]["hits"] > 0
    assert first_manifest["version"] == "0.7.0"
    assert first_manifest["input_schema"] == SOURCE_SCHEMA
    assert first_manifest["versions"]["cache"] == 5
    assert first_manifest["versions"]["candidate_state"] == (
        "candidate-components-v4"
    )
    assert first_manifest["versions"]["execution_plan"] == "execution-plan-v3"
    assert "input_format" not in first_manifest
    assert "input_granularity_seconds" not in first_manifest
    assert "pricing" not in first_manifest
    assert set(DISCOVERY_PARQUET_ARTIFACTS) <= {
        path.name for path in out.glob("*.parquet")
    }
    assert (out / "graph_snapshot.json").is_file()
    assert (out / "reports" / "coverage.md").is_file()

    db = DuckDB()
    try:
        methods = {
            str(row["discovery_method"])
            for row in db.rows(
                f"""
                SELECT DISTINCT discovery_method
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                """
            )
        }
        stale_generative_bases = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out / "logic_edges.parquet")}')
                WHERE discovery_method = 'generative_model'
                  AND edge_basis != 'generative_model_classifier'
                """
            )
            or 0
        )
        source_schemas = {
            str(row["source_schema"])
            for row in db.rows(
                f"""
                SELECT DISTINCT source_schema
                FROM read_parquet('{q(out / "propositions.parquet")}')
                """
            )
        }
        pair_ids = list(
            db.rows(
                f"""
                SELECT list(node_id ORDER BY outcome_index) AS node_ids
                FROM read_parquet('{q(out / "nodes.parquet")}')
                GROUP BY market_id
                HAVING count(*) = 2
                ORDER BY market_id
                LIMIT 1
                """
            )[0]["node_ids"]
        )
    finally:
        db.close()
    assert "llm" not in methods
    assert methods <= {"deterministic", "generative_model", "nli"}
    assert stale_generative_bases == 0
    assert source_schemas == {SOURCE_SCHEMA}

    assert main(["search", "--out", str(out), "--query", "Will"]) == 0
    assert main(["nodes", "--out", str(out), "--top", "5"]) == 0
    assert (
        main(
            [
                "edges",
                "--out",
                str(out),
                "--edge-type",
                "complement",
                "--top",
                "5",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "condition",
                "--out",
                str(out),
                "--a",
                str(pair_ids[0]),
                "--b",
                str(pair_ids[1]),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "explain",
                "--out",
                str(out),
                "--node",
                str(pair_ids[0]),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "explain-edge",
                "--out",
                str(out),
                "--src",
                str(pair_ids[0]),
                "--dst",
                str(pair_ids[1]),
                "--edge-type",
                "complement",
            ]
        )
        == 0
    )
    assert main(["benchmark-summary", "--out", str(out)]) == 0


def test_incompatible_incremental_baseline_has_no_migration_path(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _write_catalog(catalog)
    (baseline / "build_manifest.json").write_text(
        json.dumps(
            {
                "command": "discover",
                "version": "0.6.0",
                "versions": {"candidate_state": "candidate-components-v3"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Incremental baseline is incompatible"):
        discover(
            catalog,
            tmp_path / "out",
            config=DiscoveryConfig(
                incremental_from=baseline,
                max_propositions=2,
            ),
            _client=_FakeClient(),
            _embedder=_embeddings,
        )


def test_manifest_is_only_written_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    out = tmp_path / "out"
    _write_catalog(catalog)

    def fail_manifest(*_: object, **__: object) -> None:
        raise OSError("simulated manifest failure")

    monkeypatch.setattr(
        discovery_module,
        "_write_manifest_last",
        fail_manifest,
    )
    with pytest.raises(OSError, match="simulated manifest failure"):
        discover(
            catalog,
            out,
            config=DiscoveryConfig(
                cache_dir=tmp_path / "cache",
                top_k=1,
                max_propositions=4,
                max_candidates=20,
                max_llm_pairs=2,
            ),
            _client=_FakeClient(),
            _embedder=_embeddings,
        )
    assert not (out / "build_manifest.json").exists()
