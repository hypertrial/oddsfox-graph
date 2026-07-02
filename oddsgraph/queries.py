from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path


class DuckDB:
    def __init__(self, database: Path | str = ":memory:") -> None:
        self.database = str(database)
        self._conn = None
        try:
            import duckdb  # type: ignore
        except ModuleNotFoundError:
            pass
        else:
            self._conn = duckdb.connect(self.database)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()

    def execute(self, sql: str) -> None:
        if self._conn is not None:
            self._conn.execute(sql)
            return
        self._cli(sql)

    def rows(self, sql: str) -> list[dict[str, str]]:
        if self._conn is not None:
            rel = self._conn.execute(sql)
            cols = [d[0] for d in rel.description]
            return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        out = self._cli(sql)
        return list(csv.DictReader(out.splitlines()))

    def scalar(self, sql: str) -> str | int | float | None:
        rows = self.rows(sql)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def _cli(self, sql: str) -> str:
        exe = shutil.which("duckdb")
        if not exe:
            raise RuntimeError("DuckDB is required: install the Python package or put duckdb CLI on PATH")
        proc = subprocess.run(
            [exe, self.database, "-csv", "-c", sql],
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout


def q(s: str | Path) -> str:
    return str(s).replace("'", "''")
