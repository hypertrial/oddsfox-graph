"""Reduce hourly odds parquet to distinct semantic market records."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.schema import SemanticMarket

logger = logging.getLogger(__name__)


def quote_sql_literal(value: str) -> str:
    """Escape a string for safe inclusion in a DuckDB single-quoted literal."""
    return value.replace("'", "''")


def quote_path(path: Path | str) -> str:
    """Escape a filesystem path for DuckDB ``read_parquet('...')`` literals."""
    return quote_sql_literal(str(path))


def _parse_json_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON list: %s", text[:120])
        return None
    if not isinstance(parsed, list):
        return None
    return [str(v) for v in parsed]


def _rows_to_semantic_markets(rows: list[dict[str, Any]]) -> list[SemanticMarket]:
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        row["outcomes"] = _parse_json_list(row.get("outcomes"))
        row["tags"] = _parse_json_list(row.get("tags"))
        row["event_tags"] = _parse_json_list(row.get("event_tags"))
        parsed_rows.append(row)
    return [SemanticMarket(**row) for row in parsed_rows]


def list_semantic_market_event_ids(path: Path) -> list[str]:
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT DISTINCT event_id FROM read_parquet('{quote_path(path)}') "
        "ORDER BY event_id"
    ).fetchall()
    con.close()
    return [str(row[0]) for row in rows]


def select_event_ids(
    available_event_ids: list[str],
    event_ids: list[str],
    limit_events: int | None,
) -> list[str]:
    selected = list(available_event_ids)
    if event_ids:
        allowed = set(event_ids)
        selected = [event_id for event_id in selected if event_id in allowed]
    if limit_events is not None:
        selected = selected[:limit_events]
    return selected


def reduce_semantic_markets(settings: Settings) -> Path:
    settings.ensure_dirs()
    input_glob = settings.resolve_input_glob()
    output_path = settings.semantic_markets_path

    query = f"""
        SELECT
            market_id,
            any_value(event_id) AS event_id,
            any_value(event_slug) AS event_slug,
            any_value(event_title) AS event_title,
            any_value(event_description) AS event_description,
            any_value(question) AS question,
            any_value(description) AS description,
            any_value(market_slug) AS market_slug,
            any_value(sports_market_type) AS sports_market_type,
            any_value(group_item_title) AS group_item_title,
            any_value(outcomes) AS outcomes,
            any_value(tags) AS tags,
            any_value(event_tags) AS event_tags,
            any_value(game_start_time) AS game_start_time,
            any_value(end_time) AS end_time
        FROM read_parquet('{quote_path(input_glob)}')
        GROUP BY market_id
    """
    con = duckdb.connect()
    arrow_result = con.execute(query).arrow()
    con.close()
    if isinstance(arrow_result, pa.Table):
        table = arrow_result
    else:
        table = arrow_result.read_all()

    validated = _rows_to_semantic_markets(table.to_pylist())
    out_table = pa.Table.from_pylist([m.model_dump() for m in validated])
    pq.write_table(out_table, output_path)
    logger.info("Reduced %d semantic markets to %s", len(validated), output_path)
    return output_path


def load_semantic_markets(
    path: Path,
    event_ids: list[str] | None = None,
) -> list[SemanticMarket]:
    if event_ids is not None:
        if not event_ids:
            return []
        con = duckdb.connect()
        placeholders = ", ".join(
            f"'{quote_sql_literal(str(event_id))}'" for event_id in event_ids
        )
        query = f"""
            SELECT *
            FROM read_parquet('{quote_path(path)}')
            WHERE event_id IN ({placeholders})
        """
        arrow_result = con.execute(query).arrow()
        con.close()
        if isinstance(arrow_result, pa.Table):
            table = arrow_result
        else:
            table = arrow_result.read_all()
        return _rows_to_semantic_markets(table.to_pylist())

    table = pq.read_table(path)
    return _rows_to_semantic_markets(table.to_pylist())
