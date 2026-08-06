"""DuckDB query helpers for the local graph explorer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from oddsgraph.config import Settings
from oddsgraph.explorer import KNOCKOUT_STAGE_LABELS, TOPOLOGY_NODE_TYPES
from oddsgraph.explorer.presentation import (
    bracket_positions,
    short_match_label,
    stage_rank,
)


@dataclass
class GraphSlice:
    """A set of Cytoscape-ready nodes and edges."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def to_elements(self) -> list[dict[str, Any]]:
        return [*self.nodes, *self.edges]


def _quote_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def node_element(row: dict[str, Any], *, bracket: bool = False) -> dict[str, Any]:
    """Convert a parquet node row into a Cytoscape element.

    When ``bracket`` is True, attach short card labels and stage metadata.
    """
    data: dict[str, Any] = {
        "id": row["canonical_id"],
        "label": row["label"],
        "type": row["type"],
        "confidence": row["confidence"],
        "aliases": row.get("aliases") or [],
        "evidence_market_ids": row.get("evidence_market_ids") or [],
        "resolution_method": row.get("resolution_method") or "",
        "inference_method": row.get("inference_method") or "",
    }
    if bracket or row.get("stage"):
        stage = str(row.get("stage") or "")
        data["stage"] = stage
        data["stage_rank"] = stage_rank(stage)
        data["short_label"] = short_match_label(str(row["label"]))
    element: dict[str, Any] = {
        "data": data,
        "classes": row["type"],
    }
    return element


def edge_element(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a parquet edge row into a Cytoscape element."""
    edge_id = f"{row['source_id']}|{row['edge_type']}|{row['target_id']}"
    return {
        "data": {
            "id": edge_id,
            "source": row["source_id"],
            "target": row["target_id"],
            "edge_type": row["edge_type"],
            "label": row["edge_type"],
            "confidence": row["confidence"],
            "evidence_market_ids": row.get("evidence_market_ids") or [],
            "evidence_text": row.get("evidence_text") or "",
            "inference_method": row.get("inference_method") or "",
        },
        "classes": row["edge_type"],
    }


def _fetch_dicts(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    result = conn.execute(sql, params or [])
    columns = [desc[0] for desc in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def topology_elements(settings: Settings) -> GraphSlice:
    """Return the competition/stage/group/round/match/team subgraph."""
    nodes_path = _quote_path(settings.nodes_path)
    edges_path = _quote_path(settings.edges_path)
    topology_types = sorted(TOPOLOGY_NODE_TYPES)
    placeholders = ", ".join(["?"] * len(topology_types))

    with _connect() as conn:
        nodes = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{nodes_path}')
            WHERE type IN ({placeholders})
            ORDER BY type, label
            """,
            topology_types,
        )
        edges = _fetch_dicts(
            conn,
            f"""
            SELECT e.*
            FROM read_parquet('{edges_path}') e
            INNER JOIN read_parquet('{nodes_path}') s
              ON e.source_id = s.canonical_id
            INNER JOIN read_parquet('{nodes_path}') t
              ON e.target_id = t.canonical_id
            WHERE s.type IN ({placeholders})
              AND t.type IN ({placeholders})
            ORDER BY e.edge_type, e.source_id, e.target_id
            """,
            [*topology_types, *topology_types],
        )

    return GraphSlice(
        nodes=[node_element(n) for n in nodes],
        edges=[edge_element(e) for e in edges],
    )


def bracket_elements(settings: Settings) -> GraphSlice:
    """Return the 32-node knockout MATCH DAG (MATCH ADVANCES_TO MATCH only).

    Each MATCH node is enriched with ``stage``, ``stage_rank``, ``short_label``,
    and a deterministic left-to-right ``position`` for the preset bracket layout.
    """
    nodes_path = _quote_path(settings.nodes_path)
    edges_path = _quote_path(settings.edges_path)
    stage_labels = sorted(KNOCKOUT_STAGE_LABELS)
    placeholders = ", ".join(["?"] * len(stage_labels))

    with _connect() as conn:
        nodes = _fetch_dicts(
            conn,
            f"""
            SELECT DISTINCT
              m.*,
              s.label AS stage
            FROM read_parquet('{nodes_path}') m
            INNER JOIN read_parquet('{edges_path}') e
              ON e.source_id = m.canonical_id
             AND e.edge_type = 'PART_OF'
            INNER JOIN read_parquet('{nodes_path}') s
              ON e.target_id = s.canonical_id
             AND s.type = 'STAGE'
            WHERE m.type = 'MATCH'
              AND s.label IN ({placeholders})
            ORDER BY m.label
            """,
            stage_labels,
        )
        if not nodes:
            return GraphSlice()

        match_ids = [n["canonical_id"] for n in nodes]
        id_placeholders = ", ".join(["?"] * len(match_ids))
        edges = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{edges_path}')
            WHERE edge_type = 'ADVANCES_TO'
              AND source_id IN ({id_placeholders})
              AND target_id IN ({id_placeholders})
            ORDER BY source_id, target_id
            """,
            [*match_ids, *match_ids],
        )

    node_els = [node_element(n, bracket=True) for n in nodes]
    edge_els = [edge_element(e) for e in edges]
    positions = bracket_positions(node_els, edge_els)
    positioned: list[dict[str, Any]] = []
    for el in node_els:
        node_id = el["data"]["id"]
        pos = positions.get(node_id)
        if pos is not None:
            positioned.append({**el, "position": pos})
        else:
            positioned.append(el)

    return GraphSlice(nodes=positioned, edges=edge_els)


def search_nodes(settings: Settings, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search nodes by label, canonical_id, or aliases (case-insensitive)."""
    cleaned = query.strip()
    if not cleaned:
        return []
    if limit < 1:
        return []

    nodes_path = _quote_path(settings.nodes_path)
    pattern = f"%{_escape_like(cleaned)}%"
    with _connect() as conn:
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{nodes_path}')
            WHERE canonical_id ILIKE ? ESCAPE '\\'
               OR label ILIKE ? ESCAPE '\\'
               OR EXISTS (
                    SELECT 1
                    FROM UNNEST(COALESCE(aliases, [])) AS a(alias)
                    WHERE alias ILIKE ? ESCAPE '\\'
               )
            ORDER BY
              CASE
                WHEN lower(label) = lower(?) THEN 0
                WHEN lower(canonical_id) = lower(?) THEN 1
                ELSE 2
              END,
              CASE type
                WHEN 'TEAM' THEN 0
                WHEN 'MATCH' THEN 1
                WHEN 'EVENT' THEN 2
                WHEN 'GROUP' THEN 3
                WHEN 'STAGE' THEN 4
                WHEN 'ROUND' THEN 5
                WHEN 'COMPETITION' THEN 6
                WHEN 'MARKET' THEN 7
                WHEN 'OUTCOME' THEN 8
                ELSE 9
              END,
              label
            LIMIT ?
            """,
            [pattern, pattern, pattern, cleaned, cleaned, limit],
        )
    return rows


def _escape_like(value: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` for use in ILIKE patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def node_neighbors(settings: Settings, node_id: str, limit: int = 300) -> GraphSlice:
    """Return 1-hop neighbors of ``node_id`` (edges + connected nodes)."""
    if not node_id:
        return GraphSlice()
    if limit < 1:
        return GraphSlice()

    nodes_path = _quote_path(settings.nodes_path)
    edges_path = _quote_path(settings.edges_path)

    with _connect() as conn:
        edges = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{edges_path}')
            WHERE source_id = ? OR target_id = ?
            ORDER BY edge_type, source_id, target_id
            LIMIT ?
            """,
            [node_id, node_id, limit],
        )
        neighbor_ids: set[str] = {node_id}
        for edge in edges:
            neighbor_ids.add(edge["source_id"])
            neighbor_ids.add(edge["target_id"])

        if not neighbor_ids:
            return GraphSlice()

        placeholders = ", ".join(["?"] * len(neighbor_ids))
        nodes = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{nodes_path}')
            WHERE canonical_id IN ({placeholders})
            ORDER BY type, label
            """,
            sorted(neighbor_ids),
        )

    return GraphSlice(
        nodes=[node_element(n) for n in nodes],
        edges=[edge_element(e) for e in edges],
    )


def get_node(settings: Settings, node_id: str) -> dict[str, Any] | None:
    """Return a single node row, or None if missing."""
    if not node_id:
        return None
    nodes_path = _quote_path(settings.nodes_path)
    with _connect() as conn:
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{nodes_path}')
            WHERE canonical_id = ?
            LIMIT 1
            """,
            [node_id],
        )
    return rows[0] if rows else None


def get_edge(
    settings: Settings,
    source_id: str,
    target_id: str,
    edge_type: str,
) -> dict[str, Any] | None:
    """Return a single edge row, or None if missing."""
    if not source_id or not target_id or not edge_type:
        return None
    edges_path = _quote_path(settings.edges_path)
    with _connect() as conn:
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{edges_path}')
            WHERE source_id = ?
              AND target_id = ?
              AND edge_type = ?
            LIMIT 1
            """,
            [source_id, target_id, edge_type],
        )
    return rows[0] if rows else None


def graph_counts(settings: Settings) -> dict[str, Any]:
    """Return node/edge totals and per-type counts for the header."""
    report_path = settings.inference_report_path
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            node_counts = report.get("node_counts") or {}
            edge_counts = report.get("edge_counts") or {}
            if isinstance(node_counts, dict) and isinstance(edge_counts, dict):
                return {
                    "node_counts": {str(k): int(v) for k, v in node_counts.items()},
                    "edge_counts": {str(k): int(v) for k, v in edge_counts.items()},
                    "total_nodes": sum(int(v) for v in node_counts.values()),
                    "total_edges": sum(int(v) for v in edge_counts.values()),
                    "source": "inference_report",
                }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    nodes_path = _quote_path(settings.nodes_path)
    edges_path = _quote_path(settings.edges_path)
    with _connect() as conn:
        node_rows = _fetch_dicts(
            conn,
            f"""
            SELECT type AS key, COUNT(*)::INTEGER AS count
            FROM read_parquet('{nodes_path}')
            GROUP BY type
            ORDER BY type
            """,
        )
        edge_rows = _fetch_dicts(
            conn,
            f"""
            SELECT edge_type AS key, COUNT(*)::INTEGER AS count
            FROM read_parquet('{edges_path}')
            GROUP BY edge_type
            ORDER BY edge_type
            """,
        )
    node_counts = {row["key"]: row["count"] for row in node_rows}
    edge_counts = {row["key"]: row["count"] for row in edge_rows}
    return {
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "total_nodes": sum(node_counts.values()),
        "total_edges": sum(edge_counts.values()),
        "source": "parquet",
    }
