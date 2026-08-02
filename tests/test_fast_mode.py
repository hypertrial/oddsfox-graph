from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from oddsfox_graph._discovery.contracts import (
    DiscoveryConfig,
    SourceMarket,
    SourceOutcome,
)
from oddsfox_graph._discovery.extraction import extract_proposition
from oddsfox_graph._discovery import fast as fast_module
from oddsfox_graph._discovery.parsing import select_model_parse_fallback_markets
from oddsfox_graph.cli import main
from oddsfox_graph.discovery import discover
from oddsfox_graph.graph import Graph
from oddsfox_graph.operability import run_summary
from oddsfox_graph.queries import DuckDB, q


def _market(
    question: str, *, outcome: str = "Yes"
) -> tuple[SourceMarket, SourceOutcome]:
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
    assert manifest["schema_version"] == "graph-build-manifest-v1"
    assert set(manifest["published_file_hashes"]) == set(manifest["artifacts"])
    assert first["hash_io"]["files_read"] == len(manifest["artifacts"])

    db = DuckDB(first_out / "oddsfox_graph.duckdb", read_only=True)
    try:
        assert (
            db.scalar(
                f"SELECT count(*) FROM read_parquet('{q(first_out / 'model_assessments.parquet')}')"
            )
            == 0
        )
        assert db.scalar("SELECT count(*) FROM quarantined_pairs_v") == 0
        assert (
            db.scalar(
                "SELECT count(*) FROM logic_edges_v "
                "WHERE rule_id LIKE 'same_market.%' "
                "AND evidence_tier != 'source_contract'"
            )
            == 0
        )
        assert (
            db.scalar(
                "SELECT count(*) FROM logic_edges_v "
                "WHERE json_type(json_extract(source_spans_json, '$.A')) != 'ARRAY' "
                "OR json_type(json_extract(source_spans_json, '$.B')) != 'ARRAY'"
            )
            == 0
        )
        assessors = db.rows(
            f"SELECT DISTINCT assessor_type FROM read_parquet('{q(first_out / 'parse_assessments.parquet')}')"
        )
        assert assessors == [{"assessor_type": "deterministic_extractor"}]
    finally:
        db.close()

    graph = Graph.open(first_out)
    assert graph.build_mode == "fast"
    assert graph.metadata().build.build_mode == "fast"
    generic_search = graph.search("Alpha")
    assert generic_search
    assert generic_search[0].plain_claim is None
    with pytest.raises(
        ValueError,
        match="World Cup exploration and recording require a graph built",
    ):
        graph.recording_plan(limit=2, min_confidence=0.95)
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


def test_fast_unchanged_replay_skips_normalization_and_verifies_staged_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )

    def unexpected_normalization(*_: object, **__: object) -> object:
        raise AssertionError("exact replay should not normalize the input")

    monkeypatch.setattr(
        fast_module,
        "load_source_markets",
        unexpected_normalization,
    )
    replay_out = tmp_path / "replay"
    stats = discover(
        catalog,
        replay_out,
        config=DiscoveryConfig(
            mode="fast",
            incremental_from=baseline,
            progress_format="quiet",
        ),
    )
    manifest = json.loads(
        (replay_out / "build_manifest.json").read_text(encoding="utf-8")
    )

    assert stats["incremental"]["normalization_skipped"] is True
    assert "normalize_input" not in stats["stage_metrics"]
    assert stats["hash_io"]["files_verified"] == len(manifest["published_file_hashes"])
    assert stats["hash_io"]["files_read"] == (
        len(manifest["published_file_hashes"]) + 1
    )


def test_fast_replay_allows_audit_only_source_tree_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    audit_fingerprint = "f" * 64
    monkeypatch.setattr(
        fast_module,
        "source_tree_fingerprint",
        lambda: audit_fingerprint,
    )

    replay = tmp_path / "replay"
    stats = discover(
        catalog,
        replay,
        config=DiscoveryConfig(
            mode="fast",
            incremental_from=baseline,
            progress_format="quiet",
        ),
    )
    manifest = json.loads(
        (replay / "build_manifest.json").read_text(encoding="utf-8")
    )
    viewer = json.loads(
        (replay / "viewer_manifest.json").read_text(encoding="utf-8")
    )

    assert stats["incremental"]["unchanged_replay"] is True
    assert manifest["source_tree_fingerprint"] == audit_fingerprint
    assert viewer["source_tree_fingerprint"] == audit_fingerprint


def test_fast_exact_input_with_changed_selection_is_rebuilt(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )

    stats = discover(
        catalog,
        tmp_path / "bounded",
        config=DiscoveryConfig(
            mode="fast",
            incremental_from=baseline,
            max_propositions=4,
            progress_format="quiet",
        ),
    )

    assert stats["incremental"]["unchanged_replay"] is False
    assert stats["incremental"]["normalization_skipped"] is False
    assert "normalize_input" in stats["stage_metrics"]


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


@pytest.mark.parametrize("output_kind", ("ancestor", "input"))
def test_fast_mode_rejects_output_that_could_consume_its_input(
    tmp_path: Path,
    output_kind: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    catalog = source / "catalog.parquet"
    _write_catalog(catalog)
    original = catalog.read_bytes()
    marker = source / "unrelated.txt"
    marker.write_text("preserve me", encoding="utf-8")
    out = source if output_kind == "ancestor" else catalog

    with pytest.raises(ValueError, match="must not be the input file or contain"):
        discover(
            catalog,
            out,
            config=DiscoveryConfig(mode="fast", progress_format="quiet"),
        )

    assert catalog.read_bytes() == original
    assert marker.read_text(encoding="utf-8") == "preserve me"


@pytest.mark.parametrize(
    ("artifact", "corrupt"),
    (
        ("nodes.parquet", False),
        ("logic_edges.parquet", True),
        ("oddsfox_graph.duckdb", True),
        ("reports/summary.md", True),
    ),
)
def test_fast_mode_rejects_damaged_incremental_baselines(
    tmp_path: Path,
    artifact: str,
    corrupt: bool,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    target = baseline / artifact
    if corrupt:
        target.write_bytes(target.read_bytes() + b"corrupt")
    else:
        target.unlink()
    with pytest.raises(ValueError, match="baseline"):
        discover(
            catalog,
            tmp_path / "replay",
            config=DiscoveryConfig(
                mode="fast",
                incremental_from=baseline,
                progress_format="quiet",
            ),
        )


def test_fast_mode_rejects_baseline_without_complete_file_hashes(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    manifest_path = baseline / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("published_file_hashes")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="baseline"):
        discover(
            catalog,
            tmp_path / "replay",
            config=DiscoveryConfig(
                mode="fast",
                incremental_from=baseline,
                progress_format="quiet",
            ),
        )


def test_fast_replay_validates_copied_bytes_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    baseline = tmp_path / "baseline"
    discover(
        catalog,
        baseline,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "preserved").write_text("old output", encoding="utf-8")
    original_validate = fast_module._validate_incremental_baseline_files

    def validate_corrupt_copy(
        copied: Path,
        manifest: dict[str, object],
        hashes: object,
    ) -> None:
        target = copied / "nodes.parquet"
        target.write_bytes(target.read_bytes() + b"corrupt")
        original_validate(copied, manifest, hashes)  # type: ignore[arg-type]

    monkeypatch.setattr(
        fast_module,
        "_validate_incremental_baseline_files",
        validate_corrupt_copy,
    )

    with pytest.raises(ValueError, match="artifact hashes"):
        discover(
            catalog,
            out,
            config=DiscoveryConfig(
                mode="fast",
                incremental_from=baseline,
                progress_format="quiet",
            ),
        )

    assert (out / "preserved").read_text(encoding="utf-8") == "old output"


def test_fast_manifest_failure_rolls_back_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    out = tmp_path / "out"
    out.mkdir()
    (out / "preserved").write_text("old output", encoding="utf-8")

    def fail_manifest(*_: object, **__: object) -> None:
        raise OSError("simulated manifest failure")

    monkeypatch.setattr(fast_module, "write_manifest_last", fail_manifest)

    with pytest.raises(OSError, match="simulated manifest failure"):
        discover(
            catalog,
            out,
            config=DiscoveryConfig(mode="fast", progress_format="quiet"),
        )

    assert (out / "preserved").read_text(encoding="utf-8") == "old output"


def test_fast_publication_keeps_reader_on_complete_graph_at_swap_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    out = tmp_path / "out"
    discover(
        catalog,
        out,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )

    before_swap = Event()
    allow_swap = Event()
    after_swap = Event()
    allow_finish = Event()
    original_publish = fast_module.publish_directory_atomically

    def blocking_publish(
        staging: Path,
        destination: Path,
        *,
        completion_marker: str | None = None,
    ) -> fast_module.PublicationSwap:
        assert completion_marker == "build_manifest.json"
        assert Graph.open(staging).nodes()
        before_swap.set()
        assert allow_swap.wait(timeout=5)
        swap = original_publish(
            staging,
            destination,
            completion_marker=completion_marker,
        )
        after_swap.set()
        assert allow_finish.wait(timeout=5)
        return swap

    monkeypatch.setattr(
        fast_module,
        "publish_directory_atomically",
        blocking_publish,
    )
    errors: list[BaseException] = []

    def rebuild() -> None:
        try:
            discover(
                catalog,
                out,
                config=DiscoveryConfig(mode="fast", progress_format="quiet"),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = Thread(target=rebuild)
    worker.start()
    assert before_swap.wait(timeout=5)
    assert Graph.open(out).nodes()
    allow_swap.set()
    assert after_swap.wait(timeout=5)
    assert Graph.open(out).nodes()
    allow_finish.set()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []


def test_fast_keyboard_interrupt_after_swap_restores_previous_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    out = tmp_path / "out"
    discover(
        catalog,
        out,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    previous_manifest = (out / "build_manifest.json").read_bytes()

    def interrupt_finalize(_swap: fast_module.PublicationSwap) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        fast_module.PublicationSwap,
        "finalize",
        interrupt_finalize,
    )

    with pytest.raises(KeyboardInterrupt):
        discover(
            catalog,
            out,
            config=DiscoveryConfig(mode="fast", progress_format="quiet"),
        )

    assert (out / "build_manifest.json").read_bytes() == previous_manifest
    assert Graph.open(out).nodes()
    assert not list(tmp_path.glob(".out.previous-*"))
    assert not list(tmp_path.glob(".out.withdrawn-*"))


def test_cli_rejects_explicit_zero_deadline(tmp_path: Path) -> None:
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
                "--deadline-seconds",
                "0",
                "--progress-format",
                "quiet",
            ]
        )
        == 1
    )
    assert not (tmp_path / "out" / "build_manifest.json").exists()


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


def test_fast_deadline_includes_manifest_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.parquet"
    _write_catalog(catalog)
    manifest_written = False
    original_write_manifest = fast_module.write_manifest_last

    def write_manifest(*args: object, **kwargs: object) -> None:
        nonlocal manifest_written
        original_write_manifest(*args, **kwargs)
        manifest_written = True

    monkeypatch.setattr(fast_module, "write_manifest_last", write_manifest)
    monkeypatch.setattr(
        fast_module.StageRecorder,
        "runtime_seconds",
        lambda _self: 3.0 if manifest_written else 1.0,
    )
    out = tmp_path / "deadline-after-publication"
    stats = discover(
        catalog,
        out,
        config=DiscoveryConfig(
            mode="fast", deadline_seconds=2.0, progress_format="quiet"
        ),
    )
    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
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
              ('m2','Will Alpha happen?',['Yes','No'],['alpha2-yes','alpha2-no'],'e1','alpha-copy',''),
              ('m3','Who wins the cup?',['Alpha','Beta','Gamma'],['cat-a','cat-b','cat-c'],'e3','cup',''),
              ('m4','Will BTC be above $100,000?',['Yes','No'],['numeric-yes','numeric-no'],'e4','btc','')
            ) t(market_id,question,outcomes,clob_token_ids,event_id,event_slug,description)
            """
        )
        db.execute(f"COPY catalog TO '{q(path)}' (FORMAT PARQUET)")
    finally:
        db.close()


def test_fast_rules_require_authoritative_scope_and_reject_nonconvex_negations(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "numeric.parquet"
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE catalog AS SELECT * FROM (VALUES
              ('scope-a','Will attendance be above 100?',['Yes','No'],['scope-a-y','scope-a-n'],'event-a','event-a',''),
              ('scope-b','Will attendance be above 200?',['Yes','No'],['scope-b-y','scope-b-n'],'event-b','event-b',''),
              ('bounded-a','Will attendance be between 100 and 200?',['Yes','No'],['bounded-a-y','bounded-a-n'],'event-c','event-c',''),
              ('bounded-b','Will attendance be between 300 and 400?',['Yes','No'],['bounded-b-y','bounded-b-n'],'event-c','event-c',''),
              ('equal-a','Will attendance equal 100?',['Yes','No'],['equal-a-y','equal-a-n'],'event-c','event-c',''),
              ('equal-b','Will attendance equal 200?',['Yes','No'],['equal-b-y','equal-b-n'],'event-c','event-c','')
            ) t(market_id,question,outcomes,clob_token_ids,event_id,event_slug,description)
            """
        )
        db.execute(f"COPY catalog TO '{q(catalog)}' (FORMAT PARQUET)")
    finally:
        db.close()

    out = tmp_path / "numeric-out"
    discover(
        catalog,
        out,
        config=DiscoveryConfig(mode="fast", progress_format="quiet"),
    )
    graph_db = DuckDB(out / "oddsfox_graph.duckdb", read_only=True)
    try:
        cross_scope = graph_db.scalar(
            "SELECT count(*) FROM logic_edges_v "
            "WHERE (src_node_id LIKE 'scope-%' AND dst_node_id LIKE 'scope-%') "
            "AND market_id_src != market_id_dst"
        )
        unsafe_negative = graph_db.scalar(
            "SELECT count(*) FROM logic_edges_v "
            "WHERE rule_id='threshold.interval_containment.v2' "
            "AND src_node_id IN ('bounded-a-n','bounded-b-n','equal-a-n','equal-b-n') "
            "AND dst_node_id IN ('bounded-a-n','bounded-b-n','equal-a-n','equal-b-n')"
        )
        positive_disjoint = graph_db.scalar(
            "SELECT count(*) FROM logic_edges_v "
            "WHERE edge_type='mutually_exclusive' "
            "AND ((src_node_id='bounded-a-y' AND dst_node_id='bounded-b-y') "
            "OR (src_node_id='bounded-b-y' AND dst_node_id='bounded-a-y'))"
        )
    finally:
        graph_db.close()
    assert cross_scope == 0
    assert unsafe_negative == 0
    assert positive_disjoint == 1


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
