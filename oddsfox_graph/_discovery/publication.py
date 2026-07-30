from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..queries import DuckDB, q


def copy_sorted_parquet(
    db: DuckDB,
    table: str,
    path: Path,
    columns: Sequence[str],
    order_by: str,
) -> None:
    projection = ", ".join(columns)
    db.execute(
        f"""
        COPY (
            SELECT {projection}
            FROM {table}
            ORDER BY {order_by}
        ) TO '{q(path)}' (FORMAT PARQUET)
        """
    )
