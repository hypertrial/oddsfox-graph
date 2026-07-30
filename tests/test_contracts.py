from __future__ import annotations

from pathlib import Path

import pytest

from oddsfox_graph.build import (
    _create_token_minute_prices,
    _validate_final_edge_invariants,
    _validate_token_minute_prices,
)
from oddsfox_graph.contracts import validate_relation_columns
from oddsfox_graph.queries import DuckDB


def test_token_minute_prices_choose_latest_timestamp_per_minute(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "dedupe.duckdb")
    try:
        db.execute(
            """
            CREATE TABLE input_prices AS
            SELECT *
            FROM (VALUES
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(1), 1::BIGINT, 0::BIGINT, 0.40),
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(45), 45::BIGINT, 0::BIGINT, 0.45),
                ('m1', 0, 'a', 'Question A', 'Yes', 'event-1', true, false, 1.0, to_timestamp(75), 75::BIGINT, 60::BIGINT, 0.50),
                ('m1', 1, 'b', 'Question A', 'No', 'event-1', true, false, 1.0, to_timestamp(2), 2::BIGINT, 0::BIGINT, 0.60),
                ('m1', 1, 'b', 'Question A', 'No', 'event-1', true, false, 1.0, to_timestamp(55), 55::BIGINT, 0::BIGINT, 0.55)
            ) AS t(
                market_id,
                outcome_index,
                clob_token_id,
                question,
                outcome_label,
                event_slug,
                is_active,
                is_closed,
                market_volume_usd,
                odds_timestamp,
                odds_timestamp_epoch,
                odds_minute_epoch,
                price
            );

            ALTER TABLE input_prices ADD COLUMN input_canonical_team_name VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_stage_key VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_stage_rank INTEGER;
            ALTER TABLE input_prices ADD COLUMN input_market_direction VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_progression_outcome_label VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_is_progression_token BOOLEAN;
            ALTER TABLE input_prices ADD COLUMN input_opposite_clob_token_id VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_market_status VARCHAR;
            ALTER TABLE input_prices ADD COLUMN input_is_still_alive BOOLEAN;
            """
        )
        _create_token_minute_prices(db)
        _validate_token_minute_prices(db)
        validate_relation_columns(db, "token_minute_prices")
        rows = db.rows(
            """
            SELECT
                clob_token_id,
                odds_minute_epoch,
                odds_timestamp_epoch,
                price::DOUBLE AS price
            FROM token_minute_prices
            ORDER BY clob_token_id, odds_minute_epoch
            """
        )
        assert rows == [
            {"clob_token_id": "a", "odds_minute_epoch": 0, "odds_timestamp_epoch": 45, "price": 0.45},
            {"clob_token_id": "a", "odds_minute_epoch": 60, "odds_timestamp_epoch": 75, "price": 0.50},
            {"clob_token_id": "b", "odds_minute_epoch": 0, "odds_timestamp_epoch": 55, "price": 0.55},
        ]
    finally:
        db.close()


def test_validate_final_edge_invariants_rejects_duplicates(tmp_path: Path) -> None:
    db = DuckDB(tmp_path / "edges.duckdb")
    try:
        db.execute(
            """
            CREATE TABLE logic_edges_v AS
            SELECT * FROM (VALUES
                ('a', 'b', 'complement', 'same_market', 1.0, 'm1', 'm1', 'e1', 'e1', 'same market'),
                ('a', 'b', 'complement', 'same_market', 1.0, 'm1', 'm1', 'e1', 'e1', 'same market')
            ) AS t(
                src_node_id, dst_node_id, edge_type, edge_basis, confidence,
                market_id_src, market_id_dst, event_slug_src, event_slug_dst, evidence
            );
            """
        )
        with pytest.raises(RuntimeError, match="duplicate logic edges"):
            _validate_final_edge_invariants(db)
    finally:
        db.close()
