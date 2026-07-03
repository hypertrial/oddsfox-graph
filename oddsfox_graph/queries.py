from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb


class DuckDB:
    def __init__(self, database: Path | str = ":memory:") -> None:
        self.database = str(database)
        self._conn = duckdb.connect(self.database)

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None:
        if params is None:
            self._conn.execute(sql)
            return
        self._conn.execute(sql, params)

    def rows(self, sql: str, params: Sequence[object] | None = None) -> list[dict[str, object]]:
        rel = self._conn.execute(sql) if params is None else self._conn.execute(sql, params)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]

    def scalar(self, sql: str, params: Sequence[object] | None = None) -> str | int | float | None:
        rows = self.rows(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))


def q(s: str | Path) -> str:
    return str(s).replace("'", "''")
