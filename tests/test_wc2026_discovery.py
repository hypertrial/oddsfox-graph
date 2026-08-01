from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oddsfox_graph._discovery import pipeline as pipeline_module
from oddsfox_graph._discovery.contracts import DiscoveryConfig
from oddsfox_graph._discovery.input import load_source_markets
from oddsfox_graph._discovery.versions import (
    SOURCE_SCHEMA,
    WC2026_QUALIFICATION_CASE_SCHEMA_VERSION,
    WC2026_QUALIFICATION_GENERATOR_VERSION,
    WC2026_SOURCE_SCHEMA,
)
from oddsfox_graph.discovery import discover
from oddsfox_graph.explorer import export_explorer
from oddsfox_graph.graph import Graph
from oddsfox_graph.qualification import (
    PUBLISHABLE_RELATIONS,
    QualificationEvaluation,
    generate_wc2026_qualification_cases,
    qualification_case_set_hash,
    qualify_catalog,
)
from oddsfox_graph.queries import DuckDB, q


def _market(
    market_id: str,
    team: str,
    stage_key: str,
    stage_rank: int,
    direction: str,
    progression_outcome: str,
) -> dict[str, object]:
    return {
        "market_id": market_id,
        "team": team,
        "stage_key": stage_key,
        "stage_rank": stage_rank,
        "direction": direction,
        "progression_outcome": progression_outcome,
    }


WC_MARKETS = (
    _market("br-r16", "Brazil", "round_of_16", 1, "advance", "reach_round_of_16"),
    _market("br-final", "Brazil", "final", 4, "advance", "reach_final"),
    _market(
        "br-survive-r32",
        "Brazil",
        "round_of_32",
        0,
        "elimination",
        "not_eliminated_in_round_of_32",
    ),
    _market("br-winner", "Brazil", "winner", 5, "winner", "win_world_cup"),
    _market(
        "ar-winner",
        "Argentina",
        "winner",
        5,
        "winner",
        "win_world_cup",
    ),
)


class _QualificationFixtureClient:
    def __init__(self, model: str) -> None:
        self.model = model

    async def preflight(self, **_: object) -> dict[str, object]:
        return {"runtime_version": "test-fixture", "context_length": 8_192}

    async def aclose(self) -> None:
        return None


def _write_wc2026(
    path: Path,
    *,
    markets: tuple[dict[str, object], ...] = WC_MARKETS,
    reverse: bool = False,
    price_offset: float = 0.0,
    break_opposite: bool = False,
    duplicate_grain: bool = False,
) -> None:
    close_epochs = {
        "round_of_32": 1783036800,
        "round_of_16": 1783382400,
        "quarterfinal": 1783728000,
        "semifinal": 1784073600,
        "final": 1784419200,
        "winner": 1784419200,
    }
    rows: list[tuple[object, ...]] = []
    for market in markets:
        direction = str(market["direction"])
        progression_label = "No" if direction == "elimination" else "Yes"
        for outcome_index, label in enumerate(("Yes", "No")):
            token = f"{market['market_id']}-{label.lower()}"
            opposite = f"{market['market_id']}-{'no' if label == 'Yes' else 'yes'}"
            if break_opposite and market["market_id"] == "br-r16" and label == "Yes":
                opposite = token
            for hour_index, epoch in enumerate((1782864000, 1782867600)):
                rows.append(
                    (
                        market["market_id"],
                        outcome_index,
                        token,
                        f"Will {market['team']} {market['progression_outcome']}?",
                        label,
                        "fifa-world-cup-2026",
                        True,
                        False,
                        100_000.0,
                        market["stage_key"],
                        market["stage_rank"],
                        market["team"],
                        direction,
                        market["progression_outcome"],
                        label == progression_label,
                        opposite,
                        "live",
                        True,
                        datetime.fromtimestamp(
                            close_epochs[str(market["stage_key"])],
                            tz=timezone.utc,
                        ),
                        datetime.fromtimestamp(epoch, tz=timezone.utc),
                        epoch,
                        0.25 + price_offset + hour_index * 0.01,
                    )
                )
    if duplicate_grain:
        rows.append(rows[0])
    if reverse:
        rows.reverse()
    db = DuckDB()
    try:
        db.execute(
            """
            CREATE TABLE wc (
                market_id VARCHAR,
                outcome_index INTEGER,
                clob_token_id VARCHAR,
                question VARCHAR,
                outcome_label VARCHAR,
                event_slug VARCHAR,
                is_active BOOLEAN,
                is_closed BOOLEAN,
                market_volume_usd DOUBLE,
                stage_key VARCHAR,
                stage_rank INTEGER,
                canonical_team_name VARCHAR,
                market_direction VARCHAR,
                progression_outcome_label VARCHAR,
                is_progression_token BOOLEAN,
                opposite_clob_token_id VARCHAR,
                market_status VARCHAR,
                is_still_alive BOOLEAN,
                end_date TIMESTAMPTZ,
                odds_hour_utc TIMESTAMPTZ,
                odds_hour_epoch BIGINT,
                close_price DOUBLE
            )
            """
        )
        db.executemany(
            "INSERT INTO wc VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        db.execute(f"COPY wc TO '{q(path)}' (FORMAT PARQUET)")
    finally:
        db.close()


def _rewrite_wc2026(source: Path, target: Path, select_sql: str) -> None:
    db = DuckDB()
    try:
        source_relation = f"read_parquet('{q(source)}')"
        db.execute(
            f"COPY ({select_sql.format(source=source_relation)}) "
            f"TO '{q(target)}' (FORMAT PARQUET)"
        )
    finally:
        db.close()


def _recording_fixture_markets() -> tuple[dict[str, object], ...]:
    stages = (
        ("round_of_32", 0, "advance", "reach_round_of_32"),
        ("round_of_16", 1, "advance", "reach_round_of_16"),
        ("quarterfinal", 2, "advance", "reach_quarterfinal"),
        ("semifinal", 3, "advance", "reach_semifinal"),
        ("final", 4, "advance", "reach_final"),
        ("winner", 5, "winner", "win_world_cup"),
    )
    return tuple(
        _market(
            f"team-{team_index:02d}-{stage_key}",
            f"Team {team_index:02d}",
            stage_key,
            stage_rank,
            direction,
            progression_outcome,
        )
        for team_index in range(1, 13)
        for stage_key, stage_rank, direction, progression_outcome in stages
    )


def test_wc2026_profile_collapses_hours_and_has_price_independent_semantics(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.parquet"
    second_path = tmp_path / "second.parquet"
    _write_wc2026(first_path)
    _write_wc2026(second_path, reverse=True, price_offset=0.2)

    first = load_source_markets(first_path)
    second = load_source_markets(second_path, input_profile=WC2026_SOURCE_SCHEMA)
    assert first[0] == second[0] == WC2026_SOURCE_SCHEMA
    assert first[1] == second[1] == 20
    assert len(first[2]) == len(second[2]) == 5
    assert first[3]["normalized_semantic_fingerprint"] == second[3][
        "normalized_semantic_fingerprint"
    ]
    survive = next(market for market in first[2] if market.market_id == "br-survive-r32")
    assert survive.stage_rank == 0
    assert survive.progression_level == 1
    assert next(outcome for outcome in survive.outcomes if outcome.outcome == "No").is_progression
    assert survive.time_start is None
    assert survive.time_end is None
    assert survive.market_close_time == datetime.fromtimestamp(
        1783036800, tz=timezone.utc
    )
    assert first[3]["truncated"] is False
    assert first[3]["teams"] == 2
    assert first[3]["source"] == "oddsfox-pipeline"
    assert first[3]["stage_keys"] == [
        "final",
        "round_of_16",
        "round_of_32",
        "winner",
    ]


def test_wc2026_profile_accepts_current_pipeline_contract_without_close_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "with-close.parquet"
    current_pipeline = tmp_path / "current-pipeline.parquet"
    out = tmp_path / "graph"
    _write_wc2026(source)
    _rewrite_wc2026(
        source,
        current_pipeline,
        "SELECT * EXCLUDE(end_date) FROM {source}",
    )

    _, _, markets, _ = load_source_markets(
        current_pipeline,
        input_profile=WC2026_SOURCE_SCHEMA,
    )
    assert all(market.market_close_time is None for market in markets)

    discover(
        current_pipeline,
        out,
        config=DiscoveryConfig(
            mode="fast",
            input_profile=WC2026_SOURCE_SCHEMA,
            progress_format="quiet",
        ),
    )
    graph = Graph.open(out)
    search_results = graph.search("Brazil", top=20)
    search_labels = [item.plain_claim for item in search_results]
    assert len(search_labels) == len(set(search_labels))
    assert graph.search("Brazil reaches the round of 16")[0].plain_claim == (
        "Brazil reaches the round of 16"
    )
    proposition_view = graph.overview("proposition", edge_mode="essential")
    assert proposition_view.layout_mode == "progression"
    assert {node.domain for node in proposition_view.nodes} == {
        "Argentina",
        "Brazil",
    }
    assert all(node.market_close_epoch is None for node in proposition_view.nodes)
    assert all(node.stage_key is not None for node in proposition_view.nodes)
    assert all(node.progression_level is not None for node in proposition_view.nodes)
    assert all(
        node.x
        == node.progression_level * 260
        + (-42 if node.progression_outcome else 42)
        for node in proposition_view.nodes
        if node.progression_level is not None
    )
    for market_id in {node.market_id for node in proposition_view.nodes}:
        pair = [node for node in proposition_view.nodes if node.market_id == market_id]
        assert len(pair) == 2
        assert abs(pair[0].x - pair[1].x) == 84
        assert pair[0].y == pair[1].y
    assert graph.market("br-r16").market_close_epoch is None
    static = tmp_path / "static"
    manifest = export_explorer(out, static, scope="graph")
    assert manifest["schema_version"] == "static-explorer-v4"
    db = DuckDB()
    try:
        assert db.scalar(
            f"SELECT count(*) FROM read_parquet("
            f"'{q(static / 'snapshot_claims.parquet')}') "
            "WHERE market_close_epoch IS NULL"
        ) == 10
    finally:
        db.close()


def test_wc2026_qualification_contract_is_profile_specific_and_disjoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wc.parquet"
    _write_wc2026(source)
    _, _, markets, _ = load_source_markets(
        source,
        input_profile=WC2026_SOURCE_SCHEMA,
    )

    first = generate_wc2026_qualification_cases(markets, seed=7)
    second = generate_wc2026_qualification_cases(list(reversed(markets)), seed=7)

    assert qualification_case_set_hash(first) == qualification_case_set_hash(second)
    assert sum(row["record_type"] == "parse" for row in first) == len(markets)
    assert sum(row["record_type"] == "pair" for row in first) == 5_000
    assert {row["schema_version"] for row in first} == {
        WC2026_QUALIFICATION_CASE_SCHEMA_VERSION
    }
    assert {row["generator_version"] for row in first} == {
        WC2026_QUALIFICATION_GENERATOR_VERSION
    }
    partition_markets = {
        partition: {
            market_id
            for row in first
            if row["partition"] == partition
            for market_id in row["source_market_ids"]
        }
        for partition in ("selection", "validation")
    }
    assert partition_markets["selection"]
    assert partition_markets["validation"]
    assert partition_markets["selection"].isdisjoint(
        partition_markets["validation"]
    )
    unrelated_payloads = [
        json.loads(str(row["payload_json"]))
        for row in first
        if row["expected_relation"] == "unrelated"
    ]
    assert unrelated_payloads
    assert all(
        payload["proposition_A"]["event_scope"]
        != payload["proposition_B"]["event_scope"]
        and payload["proposition_A"]["subject"]
        != payload["proposition_B"]["subject"]
        for payload in unrelated_payloads
    )


def test_qualify_threads_wc2026_profile_and_publishes_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wc.parquet"
    _write_wc2026(source)
    wrong_out = tmp_path / "wrong-profile"
    with pytest.raises(ValueError, match=f"does not match {SOURCE_SCHEMA}"):
        qualify_catalog(
            source,
            wrong_out,
            config=DiscoveryConfig(
                input_profile=SOURCE_SCHEMA,
                progress_format="quiet",
            ),
        )
    assert not wrong_out.exists()

    out = tmp_path / "qualification"
    config = DiscoveryConfig(
        input_profile=WC2026_SOURCE_SCHEMA,
        progress_format="quiet",
    )
    report = qualify_catalog(
        source,
        out,
        config=config,
        _primary_client=_QualificationFixtureClient(config.primary_model),
        _verifier_client=_QualificationFixtureClient(config.verifier_model),
    )
    profile = json.loads(
        (out / "automation_profile.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "AUTOMATION_VALIDATED"
    assert report["input_profile"] == WC2026_SOURCE_SCHEMA
    assert report["case_schema_version"] == (
        WC2026_QUALIFICATION_CASE_SCHEMA_VERSION
    )
    assert profile["qualification_generator_version"] == (
        WC2026_QUALIFICATION_GENERATOR_VERSION
    )


def test_non_injected_qualify_publishes_wc2026_case_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "wc.parquet"
    out = tmp_path / "qualification"
    _write_wc2026(source)
    config = DiscoveryConfig(
        input_profile=WC2026_SOURCE_SCHEMA,
        progress_format="quiet",
    )
    inference = pipeline_module._prepare_inference_context(
        config,
        out,
        _QualificationFixtureClient(config.primary_model),
        _QualificationFixtureClient(config.verifier_model),
    )
    captured: dict[str, object] = {}

    def run_cases(cases: object, *_: object) -> list[object]:
        assert isinstance(cases, list)
        captured["case_count"] = len(cases)
        captured["schemas"] = {row["schema_version"] for row in cases}
        return []

    def evaluate(cases: object, predictions: object) -> QualificationEvaluation:
        assert isinstance(cases, list)
        assert predictions == []
        relations = {
            relation: {"correct": 200, "precision": 1.0}
            for relation in PUBLISHABLE_RELATIONS
        }
        return QualificationEvaluation(
            thresholds={relation: 0.95 for relation in PUBLISHABLE_RELATIONS},
            metrics={
                "primary_structured_validity": 1.0,
                "verifier_structured_validity": 1.0,
                "relations": relations,
                "semantic_accuracy_claim": False,
            },
            gates={"wc2026_contract_test": True},
            status="AUTOMATION_VALIDATED",
        )

    monkeypatch.setattr(
        pipeline_module,
        "_prepare_inference_context",
        lambda *_args, **_kwargs: inference,
    )
    monkeypatch.setattr(pipeline_module, "_run_qualification_cases", run_cases)
    monkeypatch.setattr(pipeline_module, "evaluate_qualification", evaluate)

    report = qualify_catalog(source, out, config=config)

    assert report["status"] == "AUTOMATION_VALIDATED"
    assert captured == {
        "case_count": 5_005,
        "schemas": {WC2026_QUALIFICATION_CASE_SCHEMA_VERSION},
    }
    db = DuckDB()
    try:
        rows = db.rows(
            f"SELECT count(*)::BIGINT AS n, "
            "min(schema_version) AS schema_version, "
            "min(generator_version) AS generator_version "
            f"FROM read_parquet('{q(out / 'qualification_cases.parquet')}')"
        )
    finally:
        db.close()
    assert rows == [
        {
            "n": 5_005,
            "schema_version": WC2026_QUALIFICATION_CASE_SCHEMA_VERSION,
            "generator_version": WC2026_QUALIFICATION_GENERATOR_VERSION,
        }
    ]


def test_wc2026_profile_rejects_partial_and_malformed_inputs(tmp_path: Path) -> None:
    valid = tmp_path / "valid.parquet"
    broken = tmp_path / "broken.parquet"
    duplicate = tmp_path / "duplicate.parquet"
    _write_wc2026(valid)
    _write_wc2026(broken, break_opposite=True)
    _write_wc2026(duplicate, duplicate_grain=True)

    with pytest.raises(ValueError, match="partial team progression chains"):
        load_source_markets(valid, max_propositions=4)
    with pytest.raises(ValueError, match="opposite token links"):
        load_source_markets(broken)
    with pytest.raises(ValueError, match="duplicate market/token/hour"):
        load_source_markets(duplicate)


@pytest.mark.parametrize(
    ("name", "select_sql", "message"),
    (
        (
            "missing-column",
            "SELECT * EXCLUDE(stage_key) FROM {source}",
            "missing columns: stage_key",
        ),
        (
            "wrong-type",
            "SELECT * REPLACE (stage_rank::VARCHAR AS stage_rank) FROM {source}",
            "incompatible column types: stage_rank=VARCHAR",
        ),
        (
            "wrong-close-type",
            "SELECT * REPLACE (end_date::VARCHAR AS end_date) FROM {source}",
            "incompatible column type: end_date=VARCHAR",
        ),
        (
            "non-finite-close",
            "SELECT * REPLACE ('infinity'::TIMESTAMPTZ AS end_date) FROM {source}",
            "non-finite end_date",
        ),
        (
            "token-count",
            "SELECT * FROM {source} WHERE NOT "
            "(market_id='ar-winner' AND outcome_label='No')",
            "expected 2 invariant token rows",
        ),
        (
            "outcome-index",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' AND outcome_label='No' "
            "THEN 2 ELSE outcome_index END AS outcome_index) FROM {source}",
            "indexes 0/1",
        ),
        (
            "outcome-label",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' AND outcome_label='No' "
            "THEN 'False' ELSE outcome_label END AS outcome_label) FROM {source}",
            "literal Yes/No",
        ),
        (
            "global-token",
            "SELECT * REPLACE (CASE WHEN market_id='br-winner' AND outcome_label='Yes' "
            "THEN 'ar-winner-yes' ELSE clob_token_id END AS clob_token_id) "
            "FROM {source}",
            "belongs to multiple markets",
        ),
        (
            "opposite",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' AND outcome_label='Yes' "
            "THEN clob_token_id ELSE opposite_clob_token_id END "
            "AS opposite_clob_token_id) FROM {source}",
            "opposite token links are not reciprocal",
        ),
        (
            "progression-count",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' THEN false "
            "ELSE is_progression_token END AS is_progression_token) FROM {source}",
            "exactly one progression token",
        ),
        (
            "progression-orientation",
            "SELECT * REPLACE (CASE WHEN market_id='br-survive-r32' "
            "THEN outcome_label='Yes' ELSE is_progression_token END "
            "AS is_progression_token) FROM {source}",
            "progression-token orientation is invalid",
        ),
        (
            "semantic-drift",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "AND odds_hour_epoch=1782867600 THEN question || ' changed' "
            "ELSE question END AS question) FROM {source}",
            "expected 2 invariant token rows",
        ),
        (
            "close-time-drift",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "AND odds_hour_epoch=1782867600 THEN end_date + INTERVAL 1 HOUR "
            "ELSE end_date END AS end_date) FROM {source}",
            "expected 2 invariant token rows",
        ),
        (
            "stage-key",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' THEN 'last_16' "
            "ELSE stage_key END AS stage_key) FROM {source}",
            "invalid stage_key/stage_rank",
        ),
        (
            "stage-rank",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' THEN 9 "
            "ELSE stage_rank END AS stage_rank) FROM {source}",
            "invalid stage_key/stage_rank",
        ),
        (
            "direction",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' THEN 'reach' "
            "ELSE market_direction END AS market_direction) FROM {source}",
            "invalid market_direction",
        ),
        (
            "status",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' THEN 'paused' "
            "ELSE market_status END AS market_status) FROM {source}",
            "invalid market_status",
        ),
        (
            "invalid-hour",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "THEN odds_hour_epoch + 1 ELSE odds_hour_epoch END "
            "AS odds_hour_epoch) FROM {source}",
            "invalid hourly grain",
        ),
        (
            "fractional-timestamp",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "AND outcome_label='Yes' AND odds_hour_epoch=1782864000 "
            "THEN odds_hour_utc + INTERVAL 400 MILLISECOND "
            "ELSE odds_hour_utc END AS odds_hour_utc) FROM {source}",
            "invalid hourly grain",
        ),
        (
            "second-offset-timestamp",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "AND outcome_label='Yes' AND odds_hour_epoch=1782864000 "
            "THEN odds_hour_utc + INTERVAL 1 SECOND "
            "ELSE odds_hour_utc END AS odds_hour_utc) FROM {source}",
            "invalid hourly grain",
        ),
        (
            "null-hour",
            "SELECT * REPLACE (CASE WHEN market_id='br-r16' "
            "THEN NULL::BIGINT ELSE odds_hour_epoch END "
            "AS odds_hour_epoch) FROM {source}",
            "null or empty required fields",
        ),
    ),
)
def test_wc2026_validation_matrix_is_strict_and_actionable(
    tmp_path: Path,
    name: str,
    select_sql: str,
    message: str,
) -> None:
    valid = tmp_path / "valid.parquet"
    invalid = tmp_path / f"{name}.parquet"
    _write_wc2026(valid)
    _rewrite_wc2026(valid, invalid, select_sql)

    with pytest.raises(ValueError, match=message) as raised:
        load_source_markets(invalid, input_profile=WC2026_SOURCE_SCHEMA)
    assert "export_polymarket_wc2026_graph_hourly_odds.py" in str(raised.value)


def test_generic_exact_score_token_export_is_not_a_known_profile(tmp_path: Path) -> None:
    source = tmp_path / "exact-score.parquet"
    db = DuckDB()
    try:
        db.execute(
            f"""
            COPY (
                SELECT 'm1'::VARCHAR AS market_id,
                       'Exact Score: Any Other Score?'::VARCHAR AS question,
                       'Yes'::VARCHAR AS outcome_label,
                       'token-1'::VARCHAR AS clob_token_id
            ) TO '{q(source)}' (FORMAT PARQUET)
            """
        )
    finally:
        db.close()

    with pytest.raises(ValueError, match="does not match a known schema") as raised:
        load_source_markets(source)
    assert "polymarket-market-snapshot-v1" in str(raised.value)
    assert WC2026_SOURCE_SCHEMA in str(raised.value)


def test_fast_wc2026_discovery_publishes_structured_rules(tmp_path: Path) -> None:
    source = tmp_path / "wc.parquet"
    out = tmp_path / "graph"
    _write_wc2026(source)
    stats = discover(
        source,
        out,
        config=DiscoveryConfig(
            mode="fast",
            input_profile=WC2026_SOURCE_SCHEMA,
            progress_format="quiet",
        ),
    )
    assert stats["tokens"] == 10
    manifest = json.loads((out / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input"]["schema"] == WC2026_SOURCE_SCHEMA
    assert manifest["scope"] == {
        "source": "oddsfox-pipeline",
        "scope": "wc2026",
        "universe": "knockout_progression",
        "selection": "all_valid_pipeline_wc2026_markets",
        "truncated": False,
    }
    assert manifest["versions"]["rules"] == "discovery-rules-v7"
    assert manifest["discovery_semantics_fingerprint"]
    assert manifest["source_tree_fingerprint"]
    viewer_manifest = json.loads(
        (out / "viewer_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["graph_content_fingerprint"] == viewer_manifest[
        "graph_content_fingerprint"
    ]

    db = DuckDB(out / "oddsfox_graph.duckdb", read_only=True)
    try:
        propositions = db.rows(
            "SELECT proposition_id, team_name, stage_key, stage_rank, "
            "progression_level, market_direction, progression_outcome, "
            "is_progression, market_status, opposite_clob_token_id "
            "FROM propositions_v ORDER BY proposition_id"
        )
        rules = {
            str(row["rule_id"]): int(row["n"])
            for row in db.rows(
                "SELECT rule_id, count(*)::INTEGER AS n FROM logic_edges_v "
                "GROUP BY rule_id ORDER BY rule_id"
            )
        }
        implication_rows = db.rows(
            "SELECT src_node_id, dst_node_id FROM logic_edges_v "
            "WHERE rule_id='wc2026.progression.v1'"
        )
    finally:
        db.close()
    assert len(propositions) == 10
    assert all(row["team_name"] for row in propositions)
    assert rules["same_market.binary_complement.v1"] == 5
    assert rules["wc2026.same_progression.v1"] >= 2
    assert rules["wc2026.progression.v1"] >= 2
    assert rules["wc2026.winner_exclusion.v1"] == 1
    assert not any(rule_id.startswith("time.") for rule_id in rules)
    implications = {
        (str(row["src_node_id"]), str(row["dst_node_id"]))
        for row in implication_rows
    }
    assert ("br-final-yes", "br-r16-yes") in implications
    assert ("br-r16-no", "br-final-no") in implications
    graph = Graph.open(out)
    assert graph.build_mode == "fast"
    assert graph.coverage()["classification_status"] == "not_applicable"
    assert graph.coverage()["classification_coverage"] is None
    assert graph.market("br-r16").market_close_epoch == 1783382400
    proposition_view = graph.overview("proposition", edge_mode="essential")
    assert all("NOT(" not in node.label for node in proposition_view.nodes)
    assert "Brazil does not reach the round of 16" in {
        node.label for node in proposition_view.nodes
    }
    assert [stage.stage_key for stage in graph.stages()] == [
        "round_of_32",
        "round_of_16",
        "quarterfinal",
        "semifinal",
        "final",
        "winner",
    ]
    round_of_16 = graph.stage("round_of_16")
    assert {market.market_id for market in round_of_16.markets} == {
        "br-r16",
        "br-survive-r32",
    }
    assert "round_of_32" not in graph.team("brazil").summary.stage_keys
    assert graph.stage("quarterfinal").markets == ()
    viewer_manifest["graph_content_fingerprint"] = "0" * 64
    (out / "viewer_manifest.json").write_text(
        json.dumps(viewer_manifest), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="viewer artifacts are incompatible"):
        Graph.open(out)


def test_recording_plan_ignores_winner_clique_for_story_diversity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wc-recording.parquet"
    out = tmp_path / "graph"
    _write_wc2026(source, markets=_recording_fixture_markets())
    discover(
        source,
        out,
        config=DiscoveryConfig(
            mode="fast",
            input_profile=WC2026_SOURCE_SCHEMA,
            progress_format="quiet",
        ),
    )

    graph = Graph.open(out)
    proposition_view = graph.overview("proposition", edge_mode="essential")
    expected_teams = {f"Team {index:02d}" for index in range(1, 13)}
    assert proposition_view.layout_mode == "close_time"
    assert {node.domain for node in proposition_view.nodes} == expected_teams
    assert len(proposition_view.nodes) == 144
    close_columns = [
        [
            node.x
            for node in proposition_view.nodes
            if node.market_close_epoch == epoch
        ]
        for epoch in sorted(
            {
                node.market_close_epoch
                for node in proposition_view.nodes
                if node.market_close_epoch is not None
            }
        )
    ]
    assert all(
        max(earlier) < min(later)
        for earlier, later in zip(close_columns, close_columns[1:])
    )
    for market_id in {node.market_id for node in proposition_view.nodes}:
        pair = [node for node in proposition_view.nodes if node.market_id == market_id]
        assert len(pair) == 2
        assert abs(pair[0].x - pair[1].x) == 84
        assert pair[0].y == pair[1].y

    plan = graph.recording_plan(limit=6)

    assert len(plan.highlights) == 6
    assert len({highlight.source_team_name for highlight in plan.highlights}) == 6
    assert len({highlight.template_key for highlight in plan.highlights}) == 6
    assert len({highlight.component_id for highlight in plan.highlights}) == 1
    assert all(highlight.confidence >= 0.95 for highlight in plan.highlights)
    assert plan.excluded_pathological == 0
    assert 3 * 30 + len(plan.highlights) * 7 * 30 + 3 * 30 == 1_440
