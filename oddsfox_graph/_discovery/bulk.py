from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..queries import DuckDB


BULK_INSERT_CHUNK_SIZE = 512


def create_and_fill(
    db: DuckDB,
    table: str,
    columns: dict[str, str],
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = BULK_INSERT_CHUNK_SIZE,
    temporary: bool = False,
) -> None:
    """Create a typed table and insert rows through DuckDB list-of-struct binding."""

    ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in columns.items())
    qualifier = "TEMP " if temporary else ""
    db.execute(f"CREATE {qualifier}TABLE {table} ({ddl})")
    insert_rows(db, table, columns, rows, chunk_size=chunk_size)


def insert_rows(
    db: DuckDB,
    table: str,
    columns: dict[str, str],
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = BULK_INSERT_CHUNK_SIZE,
) -> None:
    """Insert rows into an existing typed table in bounded chunks."""
    if not rows:
        return
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    names = list(columns)
    projection = ", ".join(f"row.{name}" for name in names)
    sql = f"INSERT INTO {table} SELECT {projection} FROM unnest(?) AS batch(row)"
    for start in range(0, len(rows), chunk_size):
        chunk = [
            {name: row.get(name) for name in names}
            for row in rows[start : start + chunk_size]
        ]
        db.execute(sql, [chunk])
