from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from oddsfox_graph._discovery.contracts import DiscoveryConfig, SourceMarket, SourceOutcome
from oddsfox_graph._discovery.extraction import extract_proposition
from oddsfox_graph._discovery import fast as fast_module
from oddsfox_graph._discovery.parsing import select_model_parse_fallback_markets
from oddsfox_graph.cli import main
from oddsfox_graph.discovery import discover
from oddsfox_graph.graph import Graph
from oddsfox_graph.operability import run_summary
from oddsfox_graph.queries import DuckDB, q


def _market(question: str, *, outcome: str = "Yes") -> tuple[SourceMarket, SourceOutcome]:
    source_outcome = SourceOutcome(0, outcome, "token")
    return (
        SourceMarket(
            market_id="market",
            question=question,
            description="",
            outcomes=(source_outcome, SourceOutcome(1, "No", "token-no")),
            source_hash="source",
            event_id="event",
            event_slug="event-slug",
        ),
        source_outcome,
    )


@pytest.mark.parametrize(
    ("question", "operator", "threshold", "unit"),
    (
        ("Will BTC be above $100,000?", "greater_than", 100_000.0, "USD"),
        ("Will inflation be at most 3 percent?", "less_than_or_equal", 3.0, "percent"),
        ("Will rainfall equal to 12?", "equal", 12.0, None),
    ),
)
def test_strict_numeric_extraction(
    question: str,
    operator: str,
    threshold: float,
    unit: str | None,
) -> None:
    market, outcome = _market(question)
    extracted = extract_proposition(market, outcome)
    assert extracted.status == "exact"
    assert extracted.operator == operator
    assert extracted.threshold == threshold
    assert extracted.unit == unit
    assert extracted.spans


def test_strict_bounded_deadline_stage_and_winner_guards() -> None:
    bounded_market, outcome = _market("Will inflation be between 2 and 4 percent?")
    bounded = extract_proposition(bounded_market, outcome)
    assert (bounded.interval_low, bounded.interval_high, bounded.unit) == (
        2.0,
        4.0,
        "percent",
    )

    deadline_market, deadline_outcome = _market(
        "Will the bill pass by January 2, 2027?"
    )
    deadline = extract_proposition(deadline_market, deadline_outcome)
    assert deadline.time_end is not None
    assert deadline.time_end.isoformat().startswith("2027-01-02")

    stage_market, stage_outcome = _market("Will Alpha reach the semifinal?")
    assert extract_proposition(stage_market, stage_outcome).stage == "semifinal"

    winner_market, winner_outcome = _market("Will Alpha win the World Cup?")
    assert extract_proposition(winner_market, winner_outcome).singular_winner is True
    top_market, top_outcome = _market("Will Alpha win the most medals?")
    assert extract_proposition(top_market, top_outcome).singular_winner is False


def test_full_parse_fallback_selection_is_bounded_and_candidate_aware() -> None:
    unmatched, _ = _market("Will Alpha happen?")
    ambiguous, _ = _market("Will CPI be above 3 and between 2 and 4 percent?")
    markets = [
        replace(ambiguous, market_id="z-ambiguous", event_id="single"),
        replace(unmatched, market_id="a-shared", event_id="shared"),
        replace(unmatched, market_id="b-shared", event_id="shared"),
        replace(unmatched, market_id="c-isolated", event_id="isolated"),
    ]
    assert select_model_parse_fallback_markets(markets, limit=2) == (
        "z-ambiguous",
        "a-shared",
    )


def test_fast_mode_publishes_shared_contract_without_inference(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    first_out = tmp_path / "first"
    first = discover(
        catalog,
        first_out,
        config=DiscoveryConfig(
            mode="fast", deadline_seconds=120, progress_format="quiet"
        ),
    )
    manifest = json.loads(
        (first_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["build_mode"] == "fast"
    assert manifest["validation_status"] == "DETERMINISTIC_VALIDATED"
    assert manifest["deadline"]["met"] is True
    assert first["tokens"] == 9
    assert first["same_market_complement_edges"] == 3
    assert first["same_market_categorical_exclusion_edges"] == 3
    assert first["cross_market_deterministic_edges"] > 0
    assert first["inference_resources_loaded"] == []
    assert not (first_out / "primary_model_manifest.json").exists()
    assert not (first_out / "automation_profile.json").exists()

    db = DuckDB(first_out / "oddsfox_graph.duckdb", read_only=True)
    try:
        assert db.scalar(
            f"SELECT count(*) FROM read_parquet('{q(first_out / 'model_assessments.parquet')}')"
        ) == 0
        assert db.scalar("SELECT count(*) FROM quarantined_pairs_v") == 0
        assert db.scalar(
            "SELECT count(*) FROM logic_edges_v "
            "WHERE rule_id LIKE 'same_market.%' "
            "AND evidence_tier != 'source_contract'"
        ) == 0
        assert db.scalar(
            "SELECT count(*) FROM logic_edges_v "
            "WHERE json_type(json_extract(source_spans_json, '$.A')) != 'ARRAY' "
            "OR json_type(json_extract(source_spans_json, '$.B')) != 'ARRAY'"
        ) == 0
        assessors = db.rows(
            f"SELECT DISTINCT assessor_type FROM read_parquet('{q(first_out / 'parse_assessments.parquet')}')"
        )
        assert assessors == [{"assessor_type": "deterministic_extractor"}]
    finally:
        db.close()

    graph = Graph.open(first_out)
    assert graph.build_mode == "fast"
    assert graph.metadata().build["build_mode"] == "fast"
    assert run_summary(first_out)["input_hash"] == manifest["input"]["sha256"]
    assert run_summary(first_out)["stage_timings"]
    assert graph.why_not("alpha-yes", "numeric-yes", "compatible").status in {
        "full_mode_not_run",
        "not_applicable_to_deterministic_rules",
    }

    replay_out = tmp_path / "replay"
    replay = discover(
        catalog,
        replay_out,
        config=DiscoveryConfig(
            mode="fast",
            incremental_from=first_out,
            deadline_seconds=120,
            progress_format="quiet",
        ),
    )
    replay_manifest = json.loads(
        (replay_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert replay["incremental"]["unchanged_replay"] is True
    assert replay_manifest["artifact_hashes"] == manifest["artifact_hashes"]


def test_fast_mode_rejects_full_flags_and_stale_baselines(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    assert (
        main(
            [
                "discover",
                "--mode",
                "fast",
                "--input",
                str(catalog),
                "--out",
                str(tmp_path / "out"),
                "--cache-dir",
                str(tmp_path / "cache"),
            ]
        )
        == 1
    )
    baseline = tmp_path / "stale"
    baseline.mkdir()
    (baseline / "build_manifest.json").write_text(
        json.dumps({"version": "0.10.0", "build_mode": "fast"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incompatible"):
        discover(
            catalog,
            tmp_path / "stale-out",
            config=DiscoveryConfig(mode="fast", incremental_from=baseline),
        )


@pytest.mark.parametrize("variant", ("change", "addition", "removal"))
def test_fast_changed_inputs_match_clean_rebuilds(
    tmp_path: Path,
    variant: str,
) -> None:
    baseline_input = tmp_path / "baseline.parquet"
    _write_catalog(baseline_input)
    baseline_out = tmp_path / "baseline"
    discover(
        baseline_input,
        baseline_out,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    variant_input = tmp_path / f"{variant}.parquet"
    _write_catalog_variant(baseline_input, variant_input, variant)
    incremental_out = tmp_path / f"{variant}-incremental"
    clean_out = tmp_path / f"{variant}-clean"
    discover(
        variant_input,
        incremental_out,
        config=DiscoveryConfig(
            mode="fast",
            incremental_from=baseline_out,
            progress_format="quiet",
        ),
    )
    discover(
        variant_input,
        clean_out,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    incremental_manifest = json.loads(
        (incremental_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    clean_manifest = json.loads(
        (clean_out / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert incremental_manifest["artifact_hashes"] == clean_manifest["artifact_hashes"]
    assert incremental_manifest["state_hashes"] == clean_manifest["state_hashes"]


def test_fast_deadline_miss_still_publishes_a_complete_graph(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    out = tmp_path / "deadline"
    stats = discover(
        catalog,
        out,
        config=DiscoveryConfig(
            mode="fast", deadline_seconds=0.000001, progress_format="quiet"
        ),
    )
    assert stats["deadline"]["met"] is False
    assert (out / "build_manifest.json").is_file()
    assert Graph.open(out).nodes()


def test_fast_deadline_includes_final_directory_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    published = False
    original_publish = fast_module.publish_directory_atomically

    def publish(*args: object, **kwargs: object) -> object:
        nonlocal published
        result = original_publish(*args, **kwargs)
        published = True
        return result

    monkeypatch.setattr(fast_module, "publish_directory_atomically", publish)
    monkeypatch.setattr(
        fast_module.StageRecorder,
        "runtime_seconds",
        lambda _self: 3.0 if published else 1.0,
    )
    out = tmp_path / "deadline-after-publication"
    stats = discover(
        catalog,
        out,
        config=DiscoveryConfig(
            mode="fast", deadline_seconds=2.0, progress_format="quiet"
        ),
    )
    manifest = json.loads(
        (out / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert stats["deadline"]["met"] is False
    assert manifest["deadline"]["met"] is False
    assert manifest["deadline"]["elapsed_seconds"] == 3.0


def _write_catalog(path: Path) -> None:
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE catalog AS SELECT * FROM (VALUES
              ('m1','Will Alpha happen?',['Yes','No'],['alpha-yes','alpha-no'],'e1','alpha',''),
              ('m2','Will Alpha happen?',['Yes','No'],['alpha2-yes','alpha2-no'],'e2','alpha-copy',''),
              ('m3','Who wins the cup?',['Alpha','Beta','Gamma'],['cat-a','cat-b','cat-c'],'e3','cup',''),
              ('m4','Will BTC be above $100,000?',['Yes','No'],['numeric-yes','numeric-no'],'e4','btc','')
            ) t(market_id,question,outcomes,clob_token_ids,event_id,event_slug,description)
            """
        )
        db.execute(f"COPY catalog TO '{q(path)}' (FORMAT PARQUET)")
    finally:
        db.close()


def _write_catalog_variant(source: Path, target: Path, variant: str) -> None:
    db = DuckDB()
    try:
        source_sql = f"read_parquet('{q(source)}')"
        if variant == "change":
            query = (
                "SELECT * REPLACE (CASE WHEN market_id='m1' "
                "THEN 'Will Alpha happen by 2030-01-01?' ELSE question END AS question) "
                f"FROM {source_sql}"
            )
        elif variant == "removal":
            query = f"SELECT * FROM {source_sql} WHERE market_id!='m1'"
        elif variant == "addition":
            query = (
                f"SELECT * FROM {source_sql} UNION ALL SELECT * REPLACE ("
                "'m-added' AS market_id, 'Will Added happen?' AS question, "
                "['added-yes','added-no']::VARCHAR[] AS clob_token_ids, "
                "'e-added' AS event_id, 'added' AS event_slug) "
                f"FROM {source_sql} WHERE market_id='m1'"
            )
        else:  # pragma: no cover - parametrization guard
            raise AssertionError(variant)
        db.execute(
            f"COPY ({query}) TO '{q(target)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        db.close()
