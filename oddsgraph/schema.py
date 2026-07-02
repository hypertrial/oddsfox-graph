from __future__ import annotations

from pathlib import Path

from .queries import DuckDB, q


REQUIRED_COLUMNS = {
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "market_volume_usd",
    "ODDS_TIMESTAMP",
    "ODDS_TIMESTAMP_EPOCH",
    "price",
}


def validate_input(db: DuckDB, path: Path) -> None:
    rows = db.rows(f"SELECT name FROM parquet_schema('{q(path)}') WHERE name != 'duckdb_schema'")
    found = {row["name"] for row in rows}
    missing = sorted(REQUIRED_COLUMNS - found)
    if missing:
        raise ValueError("Input parquet missing required columns: " + ", ".join(missing))
