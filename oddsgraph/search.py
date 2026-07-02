from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .queries import DuckDB, q


def read_rows(
    out_dir: Path,
    artifact: str,
    sql: str,
    params: Sequence[object] | None = None,
) -> list[dict[str, object]]:
    db = DuckDB()
    try:
        path = q(out_dir / artifact)
        return db.rows(sql.format(path=path), params)
    finally:
        db.close()


def search_nodes(out_dir: Path, query: str, top: int = 20) -> list[dict[str, object]]:
    lowered = query.lower()
    like = "%" + _escape_like(lowered) + "%"
    return read_rows(
        out_dir,
        "nodes.parquet",
        f"""
        SELECT node_id, market_id, outcome_label, current_price, canonical_proposition
        FROM read_parquet('{{path}}')
        WHERE lower(node_id) = ?
            OR lower(question) LIKE ? ESCAPE '!'
            OR lower(canonical_proposition) LIKE ? ESCAPE '!'
            OR lower(outcome_label) LIKE ? ESCAPE '!'
        ORDER BY current_price DESC NULLS LAST
        LIMIT {int(top)}
        """,
        [lowered, like, like, like],
    )


def resolve_node(out_dir: Path, text: str, *, require_unique: bool = False) -> str | None:
    exact = read_rows(
        out_dir,
        "nodes.parquet",
        """
        SELECT node_id
        FROM read_parquet('{path}')
        WHERE node_id = ?
        LIMIT 1
        """,
        [text],
    )
    if exact:
        return str(exact[0]["node_id"])
    if require_unique:
        matches = search_nodes(out_dir, text, 6)
        if len(matches) == 1:
            return str(matches[0]["node_id"])
        if matches:
            candidates = ", ".join(str(row["node_id"]) for row in matches[:5])
            raise ValueError(f"Ambiguous node query {text!r}; use a node_id. Candidates: {candidates}")
        return None
    matches = search_nodes(out_dir, text, 1)
    return str(matches[0]["node_id"]) if matches else None


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
