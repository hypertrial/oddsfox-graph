from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .queries import DuckDB, q


COMMON_COLUMNS = {
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "market_volume_usd",
}

MINUTELY_REQUIRED_COLUMNS = {
    *COMMON_COLUMNS,
    "odds_timestamp",
    "odds_timestamp_epoch",
    "price",
}

HOURLY_REQUIRED_COLUMNS = {
    *COMMON_COLUMNS,
    "odds_hour_utc",
    "odds_hour_epoch",
    "close_price",
}

REQUIRED_COLUMNS = MINUTELY_REQUIRED_COLUMNS

TABLE_REQUIRED_COLUMNS = {
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "market_volume_usd",
    "odds_timestamp",
    "odds_timestamp_epoch",
    "price",
}


@dataclass(frozen=True)
class InputFormat:
    name: str
    required_columns: frozenset[str]
    source_timestamp_column: str
    source_epoch_column: str
    source_price_column: str
    granularity_seconds: int

    @property
    def minute_epoch_sql(self) -> str:
        if self.granularity_seconds == 60:
            return f"CAST(floor({self.source_epoch_column} / 60) * 60 AS BIGINT)"
        return f"{self.source_epoch_column}::BIGINT"


MINUTELY_INPUT = InputFormat(
    name="minutely",
    required_columns=frozenset(MINUTELY_REQUIRED_COLUMNS),
    source_timestamp_column="odds_timestamp",
    source_epoch_column="odds_timestamp_epoch",
    source_price_column="price",
    granularity_seconds=60,
)

HOURLY_INPUT = InputFormat(
    name="hourly",
    required_columns=frozenset(HOURLY_REQUIRED_COLUMNS),
    source_timestamp_column="odds_hour_utc",
    source_epoch_column="odds_hour_epoch",
    source_price_column="close_price",
    granularity_seconds=3600,
)

INPUT_FORMATS = (MINUTELY_INPUT, HOURLY_INPUT)


def validate_input(db: DuckDB, path: Path) -> None:
    input_format = validate_input_schema(db, path)
    create_input_prices(db, path, table="input_prices_validation", input_format=input_format)
    validate_input_table(db, "input_prices_validation")


def validate_input_schema(db: DuckDB, path: Path) -> InputFormat:
    return detect_input_format(db, path)


def create_input_prices(
    db: DuckDB,
    path: Path,
    *,
    table: str = "input_prices",
    input_format: InputFormat | None = None,
) -> InputFormat:
    input_format = input_format or detect_input_format(db, path)
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE {table} AS
        SELECT
            market_id,
            outcome_index,
            clob_token_id,
            question,
            outcome_label,
            event_slug,
            is_active,
            is_closed,
            market_volume_usd,
            {input_format.source_timestamp_column} AS odds_timestamp,
            {input_format.source_epoch_column}::BIGINT AS odds_timestamp_epoch,
            {input_format.minute_epoch_sql} AS odds_minute_epoch,
            {input_format.source_price_column} AS price
        FROM read_parquet('{q(path)}');
    """)
    return input_format


def detect_input_format(db: DuckDB, path: Path) -> InputFormat:
    found = _schema_columns(db, path)
    for input_format in INPUT_FORMATS:
        if input_format.required_columns <= found:
            return input_format

    minutely_missing = sorted(MINUTELY_REQUIRED_COLUMNS - found)
    hourly_missing = sorted(HOURLY_REQUIRED_COLUMNS - found)
    raise ValueError(
        "Input parquet missing required columns for supported formats: "
        f"minutely missing {', '.join(minutely_missing)}; "
        f"hourly missing {', '.join(hourly_missing)}"
    )


def _schema_columns(db: DuckDB, path: Path) -> set[str]:
    rows = db.rows(f"SELECT name FROM parquet_schema('{q(path)}') WHERE name != 'duckdb_schema'")
    return {str(row["name"]).lower() for row in rows}


def validate_input_table(db: DuckDB, table: str = "input_prices") -> None:
    failures = [
        ("null required values", _count(db, f"""
            SELECT count(*)
            FROM {table}
            WHERE {" OR ".join(f"{col} IS NULL" for col in sorted(TABLE_REQUIRED_COLUMNS))}
        """), "rows"),
        ("prices outside [0, 1]", _count(db, f"""
            SELECT count(*)
            FROM {table}
            WHERE price < 0 OR price > 1
        """), "rows"),
        ("duplicate token timestamp rows", _count(db, f"""
            SELECT count(*)
            FROM (
                SELECT clob_token_id, odds_timestamp_epoch
                FROM {table}
                GROUP BY 1, 2
                HAVING count(*) > 1
            )
        """), "groups"),
        ("unstable token metadata", _count(db, f"""
            SELECT count(*)
            FROM (
                SELECT clob_token_id
                FROM {table}
                GROUP BY clob_token_id
                HAVING count(DISTINCT market_id) > 1
                    OR count(DISTINCT outcome_index) > 1
                    OR count(DISTINCT question) > 1
                    OR count(DISTINCT outcome_label) > 1
                    OR count(DISTINCT event_slug) > 1
                    OR count(DISTINCT is_active) > 1
                    OR count(DISTINCT is_closed) > 1
                    OR count(DISTINCT market_volume_usd) > 1
            )
        """), "tokens"),
        ("markets with fewer than 2 tokens", _count(db, f"""
            SELECT count(*)
            FROM (
                SELECT market_id
                FROM {table}
                GROUP BY market_id
                HAVING count(DISTINCT clob_token_id) < 2
            )
        """), "markets"),
        ("markets without complete current minute", _count(db, f"""
            WITH market_tokens AS (
                SELECT market_id, count(DISTINCT clob_token_id) AS expected_tokens
                FROM {table}
                GROUP BY market_id
            ),
            complete_markets AS (
                SELECT c.market_id
                FROM (
                    SELECT
                        market_id,
                        odds_minute_epoch,
                        count(DISTINCT clob_token_id) AS token_count
                    FROM {table}
                    GROUP BY 1, 2
                ) c
                JOIN market_tokens t USING (market_id)
                WHERE c.token_count = t.expected_tokens
                GROUP BY c.market_id
            )
            SELECT count(*)
            FROM market_tokens t
            LEFT JOIN complete_markets c USING (market_id)
            WHERE c.market_id IS NULL
        """), "markets"),
    ]
    failed = [f"{name}: {count} {unit}" for name, count, unit in failures if count]
    if failed:
        raise ValueError("Input parquet failed validation: " + "; ".join(failed))


def _count(db: DuckDB, sql: str) -> int:
    return int(db.scalar(sql) or 0)
