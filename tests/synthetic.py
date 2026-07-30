from __future__ import annotations

from pathlib import Path
from typing import Any

from oddsfox_graph.queries import DuckDB, q
from oddsfox_graph.sql import values_rows_sql


MARKET_COLUMNS = [
    "market_id",
    "question",
    "event_slug",
    "yes_price",
    "no_price",
    "volume",
    "minute_count",
    "start_epoch",
]

MINI_WC2026_MARKET_COLUMNS = [
    "market_id",
    "question",
    "event_slug",
    "yes_token",
    "no_token",
    "yes_price",
    "no_price",
    "volume",
    "minute_count",
    "start_epoch",
]

HOURLY_MARKET_COLUMNS = [
    "market_id",
    "question",
    "event_slug",
    "yes_price",
    "no_price",
    "volume",
    "bucket_count",
    "start_epoch",
]


def _values(rows: list[tuple[Any, ...]], columns: list[str]) -> str:
    return values_rows_sql([dict(zip(columns, row, strict=True)) for row in rows], columns)


def write_synthetic_input(path: Path) -> None:
    markets = [
        ("comp", "Will Complement pass?", "comp-event", 0.42, 0.58, 20_000.0, 10, 0),
        ("eq_a", "Will Equivalent A happen?", "eq-event", 0.55, 0.45, 20_000.0, 10, 20_000),
        ("eq_b", "Will Equivalent B happen?", "eq-event", 0.56, 0.44, 20_000.0, 10, 20_000),
        ("dup_same_a", "Will Duplicate Semantic happen?", "dup-sem-event", 0.55, 0.45, 20_000.0, 10, 150_000),
        ("dup_same_b", "Will Duplicate Semantic happen?", "dup-sem-event", 0.56, 0.44, 20_000.0, 10, 150_000),
        ("dup_cross_a", "Will Cross Event Duplicate happen?", "dup-cross-a", 0.55, 0.45, 20_000.0, 10, 160_000),
        ("dup_cross_b", "Will Cross Event Duplicate happen?", "dup-cross-b", 0.55, 0.45, 20_000.0, 10, 160_000),
        ("winner_alpha", "Will Alpha win the 2026 FIFA World Cup?", "world-cup-winner", 0.35, 0.65, 20_000.0, 10, 170_000),
        ("winner_beta", "Will Beta win the 2026 FIFA World Cup?", "world-cup-winner", 0.25, 0.75, 20_000.0, 10, 170_000),
        ("alpha_final", "Will Alpha reach the 2026 FIFA World Cup final?", "world-cup-nation-to-reach-final", 0.55, 0.45, 20_000.0, 10, 180_000),
        ("alpha_semis", "Will Alpha reach the Semifinals at the 2026 FIFA World Cup?", "world-cup-nation-to-reach-semifinals", 0.75, 0.25, 20_000.0, 10, 190_000),
    ]
    db = DuckDB(path.with_suffix(".duckdb"))
    try:
        db.execute(
            f"""
            CREATE TABLE fixture AS
            WITH market_defs(
                market_id,
                question,
                event_slug,
                yes_price,
                no_price,
                volume,
                minute_count,
                start_epoch
            ) AS (
                VALUES
                {_values(markets, MARKET_COLUMNS)}
            ),
            minute AS (
                SELECT range::BIGINT AS i
                FROM range(10)
            ),
            binary_rows AS (
                SELECT
                    market_id,
                    outcome_index,
                    market_id || ':' || outcome_label AS clob_token_id,
                    question,
                    outcome_label,
                    event_slug,
                    true AS is_active,
                    false AS is_closed,
                    volume AS market_volume_usd,
                    to_timestamp(start_epoch + i * 60) AS ODDS_TIMESTAMP,
                    (start_epoch + i * 60)::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS price
                FROM market_defs
                JOIN minute ON i < minute_count
                CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label)
            ),
            named_rows AS (
                SELECT
                    'named' AS market_id,
                    outcome_index,
                    'named:' || outcome_label AS clob_token_id,
                    'Top goalscorer?' AS question,
                    outcome_label,
                    'named-event' AS event_slug,
                    true AS is_active,
                    false AS is_closed,
                    1.0 AS market_volume_usd,
                    to_timestamp(200000 + i * 60) AS ODDS_TIMESTAMP,
                    (200000 + i * 60)::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    CASE outcome_label WHEN 'Messi' THEN 0.55 ELSE 0.45 END AS price
                FROM (SELECT * FROM minute LIMIT 3)
                CROSS JOIN (VALUES (0, 'Messi'), (1, 'Ronaldo')) AS o(outcome_index, outcome_label)
            ),
            nary_rows AS (
                SELECT
                    'golden_boot' AS market_id,
                    outcome_index,
                    'golden_boot:' || outcome_label AS clob_token_id,
                    'Who wins Golden Boot?' AS question,
                    outcome_label,
                    'world-cup-golden-boot-winner' AS event_slug,
                    true AS is_active,
                    false AS is_closed,
                    20_000.0 AS market_volume_usd,
                    to_timestamp(220000 + i * 60) AS ODDS_TIMESTAMP,
                    (220000 + i * 60)::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                    CASE outcome_label
                        WHEN 'Alpha' THEN 0.34
                        WHEN 'Beta' THEN 0.33
                        ELSE 0.33
                    END AS price
                FROM minute
                CROSS JOIN (VALUES (0, 'Alpha'), (1, 'Beta'), (2, 'Gamma')) AS o(outcome_index, outcome_label)
            )
            SELECT * FROM binary_rows
            UNION ALL SELECT * FROM named_rows
            UNION ALL SELECT * FROM nary_rows;

            COPY fixture TO '{q(path)}' (FORMAT PARQUET);
            """
        )
    finally:
        db.close()


def write_hourly_synthetic_input(path: Path) -> None:
    markets = [
        ("hourly_comp", "Will Hourly Complement happen?", "hourly-comp-event", 0.55, 0.45, 20_000.0, 4, 0),
        (
            "bosnia_r16",
            "Will Bosnia-Herzegovina reach the Round of 16 at the 2026 FIFA World Cup?",
            "world-cup-nation-to-reach-round-of-16",
            0.60,
            0.40,
            20_000.0,
            4,
            200_000,
        ),
        (
            "bosnia_final",
            "Will Bosnia and Herzegovina reach the 2026 FIFA World Cup final?",
            "world-cup-nation-to-reach-final",
            0.30,
            0.70,
            20_000.0,
            4,
            200_000,
        ),
    ]
    db = DuckDB(path.with_suffix(".duckdb"))
    try:
        db.execute(
            f"""
            CREATE TABLE hourly_fixture AS
            WITH market_defs(
                market_id,
                question,
                event_slug,
                yes_price,
                no_price,
                volume,
                bucket_count,
                start_epoch
            ) AS (
                VALUES
                {_values(markets, HOURLY_MARKET_COLUMNS)}
            ),
            hour AS (
                SELECT range::BIGINT AS i
                FROM range(4)
            )
            SELECT
                market_id,
                outcome_index,
                market_id || ':' || outcome_label AS clob_token_id,
                question,
                outcome_label,
                event_slug,
                true AS is_active,
                false AS is_closed,
                volume AS market_volume_usd,
                to_timestamp(start_epoch + i * 3600) AS odds_hour_utc,
                (start_epoch + i * 3600)::BIGINT AS odds_hour_epoch,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS open_price,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS high_price,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS low_price,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS close_price,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS avg_price,
                1::BIGINT AS observed_points,
                (start_epoch + i * 3600)::BIGINT AS first_timestamp,
                to_timestamp(start_epoch + i * 3600) AS first_observed_at,
                (start_epoch + i * 3600)::BIGINT AS last_timestamp,
                to_timestamp(start_epoch + i * 3600) AS last_observed_at
            FROM market_defs
            JOIN hour ON i < bucket_count
            CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label);

            COPY hourly_fixture TO '{q(path)}' (FORMAT PARQUET);
            """
        )
    finally:
        db.close()


def write_mini_wc2026_oracle_input(path: Path) -> None:
    markets: list[tuple[Any, ...]] = []

    def add_market(
        market_id: str,
        question: str,
        event_slug: str,
        yes_token: str,
        no_token: str,
        yes_price: float,
        *,
        start_epoch: int,
    ) -> None:
        markets.append(
            (
                market_id,
                question,
                event_slug,
                yes_token,
                no_token,
                yes_price,
                1.0 - yes_price,
                20_000.0,
                10,
                start_epoch,
            )
        )

    add_market(
        "mini_brazil_round_16",
        "Will Brazil reach the Round of 16 at the 2026 FIFA World Cup?",
        "mini-brazil-round-16",
        "60941235333934119537308581623022145063589498358463811604437431757990716193139",
        "69254358704504551873876012384649223770132435379419074198292590735170180021451",
        0.42,
        start_epoch=0,
    )
    add_market(
        "mini_announcer_source",
        "Will the opening match announcer mention the host city before kickoff?",
        "mini-unrelated-announcer-event",
        "43210016944742792301737134223300418595113462948362079532359960011115262422579",
        "mini_announcer_source:no",
        0.41,
        start_epoch=100_000,
    )
    add_market(
        "mini_announcer_destination",
        "Will a halftime broadcast graphic show attendance above sixty thousand?",
        "mini-unrelated-announcer-event",
        "27853601490370072812708927706802149718970975520996501176000797916279903304531",
        "mini_announcer_destination:no",
        0.62,
        start_epoch=100_000,
    )

    db = DuckDB(path.with_suffix(".duckdb"))
    try:
        db.execute(
            f"""
            CREATE TABLE mini_wc2026_fixture AS
            WITH market_defs(
                market_id,
                question,
                event_slug,
                yes_token,
                no_token,
                yes_price,
                no_price,
                volume,
                minute_count,
                start_epoch
            ) AS (
                VALUES
                {_values(markets, MINI_WC2026_MARKET_COLUMNS)}
            ),
            minute AS (
                SELECT range::BIGINT AS i
                FROM range(10)
            )
            SELECT
                market_id,
                outcome_index,
                CASE outcome_label WHEN 'Yes' THEN yes_token ELSE no_token END AS clob_token_id,
                question,
                outcome_label,
                event_slug,
                true AS is_active,
                false AS is_closed,
                volume AS market_volume_usd,
                to_timestamp(start_epoch + i * 60) AS ODDS_TIMESTAMP,
                (start_epoch + i * 60)::BIGINT AS ODDS_TIMESTAMP_EPOCH,
                CASE outcome_label WHEN 'Yes' THEN yes_price ELSE no_price END AS price
            FROM market_defs
            JOIN minute ON i < minute_count
            CROSS JOIN (VALUES (0, 'Yes'), (1, 'No')) AS o(outcome_index, outcome_label);

            COPY mini_wc2026_fixture TO '{q(path)}' (FORMAT PARQUET);
            """
        )
    finally:
        db.close()
