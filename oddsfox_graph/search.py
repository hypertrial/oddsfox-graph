from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .queries import DuckDB, q


PATH_SENTINEL = "__PATH__"


def read_rows(
    out_dir: Path,
    artifact: str,
    sql: str,
    params: Sequence[object] | None = None,
) -> list[dict[str, object]]:
    db = DuckDB()
    try:
        path = q(require_artifact(out_dir, artifact))
        return db.rows(sql.replace(PATH_SENTINEL, path), params)
    finally:
        db.close()


def require_artifact(out_dir: Path, artifact: str) -> Path:
    path = out_dir / artifact
    manifest_path = out_dir / "build_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts")
        if isinstance(artifacts, list) and artifact not in set(artifacts):
            raise FileNotFoundError(f"{artifact} was not generated for this build")

    if path.exists():
        return path

    raise FileNotFoundError(f"Missing artifact {artifact} in {out_dir}")


def search_nodes(out_dir: Path, query: str, top: int = 20) -> list[dict[str, object]]:
    lowered = query.lower()
    like = "%" + _escape_like(lowered) + "%"
    return read_rows(
        out_dir,
        "nodes.parquet",
        f"""
        SELECT *
        FROM read_parquet('{PATH_SENTINEL}')
        WHERE lower(node_id) = ?
            OR lower(question) LIKE ? ESCAPE '!'
            OR lower(canonical_proposition) LIKE ? ESCAPE '!'
            OR lower(outcome_label) LIKE ? ESCAPE '!'
        ORDER BY event_slug, market_id, outcome_index
        LIMIT {int(top)}
        """,
        [lowered, like, like, like],
    )


def nodes_by_ids(out_dir: Path, node_ids: Sequence[str]) -> list[dict[str, object]]:
    if not node_ids:
        return []
    return read_rows(
        out_dir,
        "nodes.parquet",
        f"""
        SELECT *
        FROM read_parquet('{PATH_SENTINEL}')
        WHERE node_id IN (SELECT unnest(?))
        ORDER BY node_id
        """,
        [list(node_ids)],
    )


def resolve_node(out_dir: Path, text: str, *, require_unique: bool = False) -> str | None:
    exact = read_rows(
        out_dir,
        "nodes.parquet",
        f"""
        SELECT node_id
        FROM read_parquet('{PATH_SENTINEL}')
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
            raise ValueError(
                f"Ambiguous node query {text!r}; use a node_id. Candidates: {candidates}"
            )
        return None
    matches = search_nodes(out_dir, text, 1)
    return str(matches[0]["node_id"]) if matches else None


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
