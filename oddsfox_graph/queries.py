from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import duckdb


class DuckDB:
    def __init__(
        self,
        database: Path | str = ":memory:",
        *,
        read_only: bool = False,
    ) -> None:
        self.database = str(database)
        self._conn = duckdb.connect(self.database, read_only=read_only)

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None:
        if params is None:
            self._conn.execute(sql)
            return
        self._conn.execute(sql, params)

    def executemany(self, sql: str, params: Sequence[Sequence[object]]) -> None:
        self._conn.executemany(sql, params)

    def rows(self, sql: str, params: Sequence[object] | None = None) -> list[dict[str, object]]:
        rel = self._conn.execute(sql) if params is None else self._conn.execute(sql, params)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]

    def scalar(self, sql: str, params: Sequence[object] | None = None) -> str | int | float | None:
        rows = self.rows(sql, params)
        if not rows:
            return None
        return cast(str | int | float | None, next(iter(rows[0].values())))

    def iter_rows(
        self,
        sql: str,
        params: Sequence[object] | None = None,
        *,
        batch_size: int = 1_024,
    ) -> Iterator[dict[str, object]]:
        """Yield bounded query batches without materializing a full result."""

        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        relation = (
            self._conn.execute(sql)
            if params is None
            else self._conn.execute(sql, params)
        )
        columns = [description[0] for description in relation.description]
        while True:
            batch = relation.fetchmany(batch_size)
            if not batch:
                return
            for row in batch:
                yield dict(zip(columns, row, strict=True))


def q(s: str | Path) -> str:
    return str(s).replace("'", "''")
