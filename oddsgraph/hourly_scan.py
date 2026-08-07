"""Shared hourly parquet scan for knockout odds-history builders.

One DuckDB pass over the source mart yields both advance-market and
stage-reach rows. The filtered result is converted to an Arrow table, then
emitted as record batches so callers can iterate without an intermediate
full ``to_pylist`` of the DuckDB result.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import duckdb
import pyarrow as pa

from oddsgraph.propositions import REACHES_STAGE_TITLES, WORLD_CUP_WINNER_TITLE
from oddsgraph.reduce import quote_path

# Champion is modeled as wins_competition (World Cup Winner markets).
STAGE_ODDS_EVENT_TITLES: dict[str, str] = {
    **REACHES_STAGE_TITLES,
    WORLD_CUP_WINNER_TITLE.casefold(): "Champion",
}

_ADVANCE_MARKET_TYPE = "soccer_team_to_advance"

_HISTORY_SCAN_COLUMNS = (
    "market_id",
    "event_title",
    "group_item_title",
    "primary_outcome_label",
    "close_odds",
    "odds_hour_epoch",
    "game_start_time",
    "event_finished_at",
    "is_resolved",
    "winning_outcome",
    "sports_market_type",
)

# Typed NULL placeholders when a source parquet omits optional columns.
_NULL_SELECT: dict[str, str] = {
    "market_id": "CAST(NULL AS VARCHAR) AS market_id",
    "event_title": "CAST(NULL AS VARCHAR) AS event_title",
    "group_item_title": "CAST(NULL AS VARCHAR) AS group_item_title",
    "primary_outcome_label": "CAST(NULL AS VARCHAR) AS primary_outcome_label",
    "close_odds": "CAST(NULL AS DOUBLE) AS close_odds",
    "odds_hour_epoch": "CAST(NULL AS BIGINT) AS odds_hour_epoch",
    "game_start_time": "CAST(NULL AS VARCHAR) AS game_start_time",
    "event_finished_at": "CAST(NULL AS VARCHAR) AS event_finished_at",
    "is_resolved": "CAST(NULL AS BOOLEAN) AS is_resolved",
    "winning_outcome": "CAST(NULL AS VARCHAR) AS winning_outcome",
    "sports_market_type": "CAST(NULL AS VARCHAR) AS sports_market_type",
}


def _stage_title_sql_list() -> str:
    titles = sorted(STAGE_ODDS_EVENT_TITLES)
    return ", ".join(f"'{t.replace(chr(39), chr(39) + chr(39))}'" for t in titles)


def _parquet_columns(con: duckdb.DuckDBPyConnection, input_glob: str) -> set[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{quote_path(input_glob)}')"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _select_list(available: set[str]) -> str:
    parts: list[str] = []
    for name in _HISTORY_SCAN_COLUMNS:
        if name in available:
            parts.append(name)
        else:
            parts.append(_NULL_SELECT[name])
    return ", ".join(parts)


def _where_clause(available: set[str]) -> str:
    predicates = ["odds_hour_epoch IS NOT NULL", "close_odds IS NOT NULL"]
    or_parts: list[str] = []
    if "sports_market_type" in available:
        or_parts.append(f"sports_market_type = '{_ADVANCE_MARKET_TYPE}'")
    if "event_title" in available:
        title_list = _stage_title_sql_list()
        or_parts.append(f"lower(trim(event_title)) IN ({title_list})")
    if or_parts:
        predicates.append("(" + " OR ".join(or_parts) + ")")
    return " AND ".join(predicates)


def iter_history_source_batches(
    input_glob: str,
    *,
    batch_size: int = 8192,
) -> Iterator[pa.RecordBatch]:
    """Stream Arrow batches from a single parquet scan for history builders."""
    con = duckdb.connect()
    try:
        available = _parquet_columns(con, input_glob)
        if "odds_hour_epoch" not in available or "close_odds" not in available:
            return
        query = f"""
            SELECT {_select_list(available)}
            FROM read_parquet('{quote_path(input_glob)}')
            WHERE {_where_clause(available)}
            ORDER BY market_id, odds_hour_epoch
        """
        relation = con.sql(query)
        # Prefer Arrow table materialization: DuckDBPyRelation.__getattr__ can
        # treat method names as column lookups depending on the projection.
        to_arrow_table = getattr(relation, "to_arrow_table", None)
        if callable(to_arrow_table):
            table = to_arrow_table()
        else:
            table = relation.arrow()
        if not isinstance(table, pa.Table):
            table = pa.Table.from_batches(list(table))
        if table.num_rows == 0:
            return
        yield from table.to_batches(max_chunksize=max(1, int(batch_size)))
    finally:
        con.close()


def iter_history_source_rows(
    input_glob: str,
    *,
    batch_size: int = 8192,
) -> Iterator[dict[str, Any]]:
    """Yield hourly market rows from one scan (advance + stage-reach)."""
    for batch in iter_history_source_batches(input_glob, batch_size=batch_size):
        yield from batch.to_pylist()


def split_history_source_rows(
    input_glob: str,
    *,
    batch_size: int = 8192,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(advance_rows, stage_rows)`` from a single source scan."""
    advance_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    for row in iter_history_source_rows(input_glob, batch_size=batch_size):
        market_type = row.get("sports_market_type")
        if market_type == _ADVANCE_MARKET_TYPE:
            advance_rows.append(row)
        title = (row.get("event_title") or "").strip().casefold()
        if title in STAGE_ODDS_EVENT_TITLES:
            stage_rows.append(row)
    return advance_rows, stage_rows


__all__ = [
    "STAGE_ODDS_EVENT_TITLES",
    "iter_history_source_batches",
    "iter_history_source_rows",
    "split_history_source_rows",
]
