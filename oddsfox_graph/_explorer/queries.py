"""Bounded, parameterized queries for completed graph outputs."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal, cast

from .contracts import (
    ExplorerEdge,
    ExplorerMetadata,
    ExplorerNode,
    GraphFilter,
    GraphPage,
    GraphView,
    RecordingContextPruning,
    RecordingHighlight,
    RecordingPlan,
    RecordingScoreBreakdown,
)
from .. import __version__
from ..queries import DuckDB


_RECORDING_CANDIDATES_SQL = """
WITH qualified AS (
    SELECT
        e.*,
        src_node.canonical_proposition AS src_label,
        src.market_id AS src_market_id,
        src.event_key AS src_event_key,
        src.primary_domain AS src_domain,
        dst_node.canonical_proposition AS dst_label,
        dst.market_id AS dst_market_id,
        dst.event_key AS dst_event_key,
        dst.primary_domain AS dst_domain,
        coalesce(src_metric.total_degree, 0)
            + coalesce(dst_metric.total_degree, 0) AS degree_sum,
        row_number() OVER (
            PARTITION BY e.edge_type,
                CASE WHEN e.edge_type = 'implies'
                    THEN e.src_node_id
                    ELSE least(e.src_node_id, e.dst_node_id)
                END,
                CASE WHEN e.edge_type = 'implies'
                    THEN e.dst_node_id
                    ELSE greatest(e.src_node_id, e.dst_node_id)
                END
            ORDER BY e.proposal_id
        ) AS duplicate_rank
    FROM logic_edges_v e
    JOIN explorer_propositions_v src
      ON src.proposition_id = e.src_node_id
    JOIN explorer_propositions_v dst
      ON dst.proposition_id = e.dst_node_id
    JOIN nodes_table src_node
      ON src_node.node_id = e.src_node_id
    JOIN nodes_table dst_node
      ON dst_node.node_id = e.dst_node_id
    LEFT JOIN node_metrics_v src_metric
      ON src_metric.node_id = e.src_node_id
    LEFT JOIN node_metrics_v dst_metric
      ON dst_metric.node_id = e.dst_node_id
    WHERE e.edge_type != 'compatible' AND e.confidence >= ?
), deduplicated AS (
    SELECT * EXCLUDE (duplicate_rank)
    FROM qualified
    WHERE duplicate_rank = 1
), features AS (
    SELECT *,
        CASE
            WHEN src_event_key != dst_event_key THEN 1.0
            WHEN src_market_id != dst_market_id THEN 0.65
            ELSE 0.25
        END AS scope,
        CASE evidence_tier
            WHEN 'generative_consensus' THEN 1.0
            WHEN 'deterministic_rule' THEN 0.80
            WHEN 'source_contract' THEN 0.55
            ELSE 0.0
        END AS evidence_interest,
        CASE edge_type
            WHEN 'implies' THEN 1.0
            WHEN 'equivalent' THEN 0.90
            WHEN 'mutually_exclusive' THEN 0.85
            WHEN 'complement' THEN 0.60
            ELSE 0.0
        END AS relation_interest,
        CASE
            WHEN max(degree_sum) OVER () <= 0 THEN 0.0
            ELSE ln(1.0 + degree_sum) / ln(1.0 + max(degree_sum) OVER ())
        END AS structural_reach,
        count(*) OVER () AS eligible_edge_count
    FROM deduplicated
), scored AS (
    SELECT *,
        0.30 * confidence
        + 0.25 * scope
        + 0.20 * structural_reach
        + 0.15 * evidence_interest
        + 0.10 * relation_interest AS base_importance
    FROM features
)
SELECT *
FROM scored
ORDER BY base_importance DESC, proposal_id
LIMIT ?
"""


_RECORDING_CONTEXT_SQL = """
WITH qualified AS (
    SELECT e.*,
        row_number() OVER (
            PARTITION BY e.edge_type,
                CASE WHEN e.edge_type = 'implies'
                    THEN e.src_node_id
                    ELSE least(e.src_node_id, e.dst_node_id)
                END,
                CASE WHEN e.edge_type = 'implies'
                    THEN e.dst_node_id
                    ELSE greatest(e.src_node_id, e.dst_node_id)
                END
            ORDER BY e.proposal_id
        ) AS duplicate_rank
    FROM logic_edges_v e
    WHERE e.edge_type != 'compatible' AND e.confidence >= ?
), deduplicated AS (
    SELECT * EXCLUDE (duplicate_rank)
    FROM qualified
    WHERE duplicate_rank = 1
), context_edges AS (
    SELECT *
    FROM deduplicated
    WHERE proposal_id NOT IN (SELECT unnest(?))
), incident AS (
    SELECT endpoint.node_id AS context_endpoint, e.*
    FROM unnest(?) AS endpoint(node_id)
    JOIN context_edges e
      ON e.src_node_id = endpoint.node_id OR e.dst_node_id = endpoint.node_id
), ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY context_endpoint
        ORDER BY confidence DESC, edge_type, proposal_id
    ) AS context_rank
    FROM incident
)
SELECT * EXCLUDE (context_endpoint, context_rank)
FROM ranked
WHERE context_endpoint IN (SELECT unnest(?)) AND context_rank <= ?
ORDER BY confidence DESC, edge_type, proposal_id
"""


class ExplorerStore:
    """Open short-lived read-only DuckDB connections for viewer queries."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir.resolve()
        self.database_path = self.out_dir / "oddsfox_graph.duckdb"
        if not self.database_path.is_file():
            raise ValueError(f"Missing graph database {self.database_path}")

    def metadata(self) -> ExplorerMetadata:
        return ExplorerMetadata(
            package_version=__version__,
            viewer=self._read_json("viewer_manifest.json"),
            coverage=self.coverage(),
            build=self._read_json("build_manifest.json"),
        )

    def coverage(self) -> dict[str, object]:
        return self._read_json("coverage_summary.json")

    def events(
        self,
        filters: GraphFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        bounded = _bounded(limit, 1, 1_000, "event limit")
        active_filters = filters or GraphFilter()
        where, params = _event_filter(active_filters)
        if cursor is not None:
            where.append("event_key > ?")
            params.append(cursor)
        sql_where = "WHERE " + " AND ".join(where) if where else ""
        db = self._db()
        try:
            rows = db.rows(
                f"""
                SELECT * FROM event_summary_v
                {sql_where}
                ORDER BY event_key
                LIMIT ?
                """,
                [*params, bounded + 1],
            )
        finally:
            db.close()
        truncated = len(rows) > bounded
        selected = rows[:bounded]
        return GraphPage(
            rows=tuple(selected),
            next_cursor=(str(selected[-1]["event_key"]) if truncated else None),
            truncated=truncated,
        )

    def components(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        bounded = _bounded(limit, 1, 1_000, "component limit")
        params: list[object] = []
        where = ""
        if cursor is not None:
            where = "WHERE component_id > ?"
            params.append(cursor)
        db = self._db()
        try:
            rows = db.rows(
                f"""
                SELECT * FROM component_summary_v
                {where}
                ORDER BY component_id
                LIMIT ?
                """,
                [*params, bounded + 1],
            )
        finally:
            db.close()
        truncated = len(rows) > bounded
        selected = rows[:bounded]
        return GraphPage(
            rows=tuple(selected),
            next_cursor=(str(selected[-1]["component_id"]) if truncated else None),
            truncated=truncated,
        )

    def event(self, event_key: str) -> dict[str, object]:
        db = self._db()
        try:
            summary = db.rows(
                "SELECT * FROM event_summary_v WHERE event_key = ?",
                [event_key],
            )
            if not summary:
                raise KeyError(f"Unknown event {event_key!r}")
            markets = db.rows(
                """
                SELECT DISTINCT market_id, question, category, primary_domain
                FROM explorer_propositions_v
                WHERE event_key = ?
                ORDER BY market_id
                LIMIT 1001
                """,
                [event_key],
            )
            return {
                "summary": summary[0],
                "markets": markets[:1_000],
                "markets_truncated": len(markets) > 1_000,
            }
        finally:
            db.close()

    def component(self, component_id: str) -> dict[str, object]:
        db = self._db()
        try:
            summary = db.rows(
                "SELECT * FROM component_summary_v WHERE component_id = ?",
                [component_id],
            )
            if not summary:
                raise KeyError(f"Unknown component {component_id!r}")
            events = db.rows(
                """
                SELECT DISTINCT event_key
                FROM explorer_propositions_v
                WHERE component_id = ?
                ORDER BY event_key
                LIMIT 1001
                """,
                [component_id],
            )
            return {
                "summary": summary[0],
                "event_keys": [str(row["event_key"]) for row in events[:1_000]],
                "events_truncated": len(events) > 1_000,
            }
        finally:
            db.close()

    def overview(
        self,
        level: Literal["component", "event"] = "event",
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        node_limit = _bounded(max_nodes, 1, 5_000, "node limit")
        edge_limit = _bounded(max_edges, 0, 10_000, "edge limit")
        if level == "component":
            return self._component_overview(node_limit, edge_limit)
        return self._event_overview(
            filters or GraphFilter(),
            node_limit,
            edge_limit,
        )

    def neighborhood(
        self,
        node_ids: tuple[str, ...],
        *,
        hops: int = 1,
        filters: GraphFilter | None = None,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        if not node_ids:
            raise ValueError("At least one seed node is required")
        bounded_hops = _bounded(hops, 0, 4, "hop count")
        node_limit = _bounded(max_nodes, 1, 5_000, "node limit")
        edge_limit = _bounded(max_edges, 0, 10_000, "edge limit")
        active_filters = filters or GraphFilter()
        db = self._db()
        unique_node_ids = tuple(sorted(set(node_ids)))
        if len(unique_node_ids) > node_limit:
            raise ValueError(
                f"Seed node count exceeds the {node_limit}-node response limit"
            )
        selected_nodes = set(unique_node_ids)
        edge_by_id: dict[str, dict[str, object]] = {}
        frontier = set(unique_node_ids)
        truncated_nodes = False
        truncated_edges = False
        try:
            existing = db.rows(
                "SELECT node_id FROM nodes_table WHERE node_id IN (SELECT unnest(?))",
                [list(unique_node_ids)],
            )
            if len(existing) != len(unique_node_ids):
                missing = sorted(
                    set(unique_node_ids)
                    - {str(row["node_id"]) for row in existing}
                )
                raise KeyError("Unknown node(s): " + ", ".join(missing))
            for _ in range(bounded_hops):
                if not frontier:
                    break
                relation_sql, relation_params = _edge_filter(active_filters, "e")
                remaining_edges = edge_limit - len(edge_by_id)
                excluded_sql = ""
                excluded_params: list[object] = []
                if edge_by_id:
                    excluded_sql = (
                        "AND e.proposal_id NOT IN (SELECT unnest(?))"
                    )
                    excluded_params.append(sorted(edge_by_id))
                rows = db.rows(
                    f"""
                    SELECT e.*
                    FROM logic_edges_v e
                    WHERE (e.src_node_id IN (SELECT unnest(?))
                      OR e.dst_node_id IN (SELECT unnest(?)))
                      {relation_sql}
                      {excluded_sql}
                    ORDER BY e.confidence DESC, e.edge_type,
                             e.src_node_id, e.dst_node_id, e.proposal_id
                    LIMIT ?
                    """,
                    [
                        sorted(frontier),
                        sorted(frontier),
                        *relation_params,
                        *excluded_params,
                        remaining_edges + 1,
                    ],
                )
                if len(rows) > remaining_edges:
                    truncated_edges = True
                selected_rows = rows[:remaining_edges]
                ordered_new_nodes: list[str] = []
                seen_new_nodes: set[str] = set()
                for row in selected_rows:
                    for key in ("src_node_id", "dst_node_id"):
                        node_id = str(row[key])
                        if (
                            node_id not in selected_nodes
                            and node_id not in seen_new_nodes
                        ):
                            ordered_new_nodes.append(node_id)
                            seen_new_nodes.add(node_id)
                available = node_limit - len(selected_nodes)
                allowed_new_nodes = set(ordered_new_nodes[:available])
                if len(ordered_new_nodes) > available:
                    truncated_nodes = True
                    truncated_edges = True
                selected_nodes.update(allowed_new_nodes)
                for row in selected_rows:
                    if {
                        str(row["src_node_id"]),
                        str(row["dst_node_id"]),
                    } <= selected_nodes:
                        edge_by_id[str(row["proposal_id"])] = row
                frontier = allowed_new_nodes
                if truncated_nodes or truncated_edges:
                    break
            node_rows = db.rows(
                """
                SELECT n.*, m.component_id, m.total_degree,
                       m.classification_state, m.classification_coverage,
                       p.event_key
                FROM nodes_table n
                JOIN node_metrics_v m USING (node_id)
                JOIN explorer_propositions_v p ON p.proposition_id = n.node_id
                WHERE n.node_id IN (SELECT unnest(?))
                ORDER BY n.node_id
                """,
                [sorted(selected_nodes)],
            )
        finally:
            db.close()
        nodes = tuple(_proposition_node(row) for row in node_rows)
        edge_rows = sorted(
            edge_by_id.values(),
            key=lambda row: (
                -float(cast(float, row["confidence"])),
                str(row["edge_type"]),
                str(row["src_node_id"]),
                str(row["dst_node_id"]),
            ),
        )
        edges = tuple(_logic_edge(row) for row in edge_rows[:edge_limit])
        return GraphView(
            level="proposition",
            nodes=nodes,
            edges=edges,
            truncated_nodes=truncated_nodes,
            truncated_edges=truncated_edges,
            coverage=self.coverage(),
        )

    def edge(self, proposal_id: str) -> dict[str, object]:
        db = self._db()
        try:
            rows = db.rows(
                "SELECT * FROM logic_edges_v WHERE proposal_id = ?",
                [proposal_id],
            )
        finally:
            db.close()
        if not rows:
            raise KeyError(f"Unknown accepted proposal {proposal_id!r}")
        return rows[0]

    def event_graph(
        self,
        event_key: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        """Return a bounded proposition view for semantic event drill-down."""

        node_limit = _bounded(max_nodes, 1, 5_000, "node limit")
        db = self._db()
        try:
            rows = db.rows(
                """
                SELECT proposition_id
                FROM explorer_propositions_v
                WHERE event_key = ?
                ORDER BY proposition_id
                LIMIT ?
                """,
                [event_key, node_limit + 1],
            )
        finally:
            db.close()
        if not rows:
            raise KeyError(f"Unknown event {event_key!r}")
        if len(rows) > node_limit:
            raise ValueError(
                f"Event {event_key!r} exceeds the {node_limit}-node response limit"
            )
        return self.neighborhood(
            tuple(str(row["proposition_id"]) for row in rows),
            hops=1,
            filters=filters,
            max_nodes=node_limit,
            max_edges=max_edges,
        )

    def component_graph(
        self,
        component_id: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        """Return the bounded event atlas for one selected component."""

        self.component(component_id)
        return self._event_overview(
            filters or GraphFilter(),
            _bounded(max_nodes, 1, 5_000, "node limit"),
            _bounded(max_edges, 0, 10_000, "edge limit"),
            component_id=component_id,
        )

    def diagnostics(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        bounded = _bounded(limit, 1, 1_000, "diagnostic limit")
        where: list[str] = []
        params: list[object] = []
        if status:
            where.append("reason_code = ?")
            params.append(status)
        if cursor:
            where.append("quarantine_id > ?")
            params.append(cursor)
        sql_where = "WHERE " + " AND ".join(where) if where else ""
        db = self._db()
        try:
            rows = db.rows(
                f"""
                SELECT * FROM quarantined_pairs_v
                {sql_where}
                ORDER BY quarantine_id
                LIMIT ?
                """,
                [*params, bounded + 1],
            )
        finally:
            db.close()
        truncated = len(rows) > bounded
        selected = rows[:bounded]
        return GraphPage(
            rows=tuple(selected),
            next_cursor=(str(selected[-1]["quarantine_id"]) if truncated else None),
            truncated=truncated,
        )

    def recording_plan(
        self,
        *,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> RecordingPlan:
        """Rank diverse accepted edges and construct their bounded context."""

        bounded_limit = _bounded(limit, 1, 12, "recording highlight limit")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        pool_limit = min(10_000, max(1_000, bounded_limit * 200))
        db = self._db()
        try:
            candidates = db.rows(
                _RECORDING_CANDIDATES_SQL,
                [min_confidence, pool_limit],
            )
            if not candidates:
                raise ValueError(
                    "No accepted non-compatible logical edges meet the "
                    f"{min_confidence:.3f} recording confidence threshold"
                )
            selected = _select_recording_highlights(candidates, bounded_limit)
            endpoint_ids = sorted(
                {
                    str(row[key])
                    for row, _ in selected
                    for key in ("src_node_id", "dst_node_id")
                }
            )
            context_rows = db.rows(
                _RECORDING_CONTEXT_SQL,
                [
                    min_confidence,
                    sorted(str(row["proposal_id"]) for row, _ in selected),
                    endpoint_ids,
                    endpoint_ids,
                    25,
                ],
            )
            selected_by_id = {
                str(row["proposal_id"]): row for row, _ in selected
            }
            context_by_id = {
                str(row["proposal_id"]): row for row in context_rows
            }
            context_by_id.update(selected_by_id)
            retained_edges, retained_nodes = _prune_recording_context(
                context_by_id,
                frozenset(selected_by_id),
                max_nodes=750,
                max_edges=1_500,
            )
            node_rows = db.rows(
                """
                SELECT n.*, m.component_id, m.total_degree,
                       m.classification_state, m.classification_coverage,
                       p.event_key
                FROM nodes_table n
                JOIN node_metrics_v m USING (node_id)
                JOIN explorer_propositions_v p ON p.proposition_id = n.node_id
                WHERE n.node_id IN (SELECT unnest(?))
                ORDER BY n.node_id
                """,
                [sorted(retained_nodes)],
            )
        finally:
            db.close()

        metadata = self.metadata()
        viewer = metadata.viewer
        graph_fingerprint = str(
            viewer.get("graph_content_fingerprint") or "unknown"
        )
        mode = str(viewer.get("build_mode") or metadata.build.get("build_mode"))
        if mode not in {"fast", "full"}:
            raise ValueError("Graph viewer manifest has no valid build mode")
        validation_status = str(
            viewer.get("validation_status")
            or metadata.build.get("validation_status")
            or "UNKNOWN"
        )
        highlights = tuple(
            _recording_highlight(row, breakdown, rank=index)
            for index, (row, breakdown) in enumerate(selected, start=1)
        )
        edge_rows = sorted(
            retained_edges,
            key=lambda row: (
                -_float(row["confidence"]),
                str(row["edge_type"]),
                str(row["proposal_id"]),
            ),
        )
        view = GraphView(
            level="proposition",
            nodes=tuple(_proposition_node(row) for row in node_rows),
            edges=tuple(_logic_edge(row) for row in edge_rows),
            truncated_nodes=len(retained_nodes) < len(
                {
                    str(row[key])
                    for row in context_by_id.values()
                    for key in ("src_node_id", "dst_node_id")
                }
            ),
            truncated_edges=len(retained_edges) < len(context_by_id),
            coverage=self.coverage(),
        )
        candidate_nodes = {
            str(row[key])
            for row in context_by_id.values()
            for key in ("src_node_id", "dst_node_id")
        }
        eligible_count = _int(candidates[0]["eligible_edge_count"])
        return RecordingPlan(
            graph_fingerprint=graph_fingerprint,
            mode=cast(Literal["fast", "full"], mode),
            validation_status=validation_status,
            requested_limit=bounded_limit,
            min_confidence=min_confidence,
            eligible_edge_count=eligible_count,
            candidate_pool_size=len(candidates),
            highlights=highlights,
            graph=view,
            context_pruning=RecordingContextPruning(
                incident_edge_cap_per_endpoint=25,
                candidate_nodes=len(candidate_nodes),
                candidate_edges=len(context_by_id),
                retained_nodes=len(retained_nodes),
                retained_edges=len(retained_edges),
                pruned_nodes=len(candidate_nodes) - len(retained_nodes),
                pruned_edges=len(context_by_id) - len(retained_edges),
            ),
        )

    def _event_overview(
        self,
        filters: GraphFilter,
        node_limit: int,
        edge_limit: int,
        *,
        component_id: str | None = None,
    ) -> GraphView:
        where, params = _event_filter(filters)
        if component_id is not None:
            where.append("l.parent_id = ?")
            params.append(component_id)
        sql_where = "WHERE " + " AND ".join(where) if where else ""
        edge_sql, edge_params = _aggregate_edge_filter(filters)
        db = self._db()
        try:
            node_rows = db.rows(
                f"""
                WITH filtered_relations AS (
                    SELECT src_event_key, dst_event_key, edge_count
                    FROM event_relation_summary_v
                    WHERE true {edge_sql}
                ), filtered_degree AS (
                    SELECT event_key, sum(edge_count)::BIGINT AS edge_count
                    FROM (
                        SELECT src_event_key AS event_key, edge_count
                        FROM filtered_relations
                        UNION ALL
                        SELECT dst_event_key AS event_key, edge_count
                        FROM filtered_relations
                        WHERE dst_event_key != src_event_key
                    ) endpoints
                    GROUP BY event_key
                )
                SELECT e.*, l.x, l.y, l.radius,
                       l.parent_id AS component_id,
                       coalesce(d.edge_count, 0)::BIGINT AS filtered_edge_count
                FROM event_summary_v e
                JOIN visualization_layout_v l
                  ON l.layout_level = 'event' AND l.object_id = e.event_key
                LEFT JOIN filtered_degree d USING (event_key)
                {sql_where}
                ORDER BY filtered_edge_count DESC,
                         e.accepted_edge_count DESC, e.event_key
                LIMIT ?
                """,
                [*edge_params, *params, node_limit + 1],
            )
            selected = node_rows[:node_limit]
            event_ids = [str(row["event_key"]) for row in selected]
            edge_rows = (
                db.rows(
                    f"""
                    SELECT * FROM event_relation_summary_v
                    WHERE src_event_key IN (SELECT unnest(?))
                      AND dst_event_key IN (SELECT unnest(?))
                      {edge_sql}
                    ORDER BY edge_count DESC, src_event_key,
                             dst_event_key, edge_type
                    LIMIT ?
                    """,
                    [event_ids, event_ids, *edge_params, edge_limit + 1],
                )
                if event_ids
                else []
            )
        finally:
            db.close()
        return GraphView(
            level="event",
            nodes=tuple(_event_node(row) for row in selected),
            edges=tuple(_event_edge(row) for row in edge_rows[:edge_limit]),
            truncated_nodes=len(node_rows) > node_limit,
            truncated_edges=len(edge_rows) > edge_limit,
            coverage=self.coverage(),
        )

    def _component_overview(self, node_limit: int, edge_limit: int) -> GraphView:
        db = self._db()
        try:
            node_rows = db.rows(
                """
                SELECT c.*, l.x, l.y, l.radius
                FROM component_summary_v c
                JOIN visualization_layout_v l
                  ON l.layout_level = 'component'
                 AND l.object_id = c.component_id
                ORDER BY c.edge_count DESC, c.component_id
                LIMIT ?
                """,
                [node_limit + 1],
            )
            selected = node_rows[:node_limit]
            component_ids = [str(row["component_id"]) for row in selected]
            edge_rows = (
                db.rows(
                    """
                    SELECT
                        src.component_id AS source,
                        dst.component_id AS target,
                        e.edge_type,
                        count(*)::BIGINT AS edge_count,
                        avg(e.confidence)::DOUBLE AS confidence
                    FROM logic_edges_v e
                    JOIN node_components_v src ON src.node_id = e.src_node_id
                    JOIN node_components_v dst ON dst.node_id = e.dst_node_id
                    WHERE src.component_id IN (SELECT unnest(?))
                      AND dst.component_id IN (SELECT unnest(?))
                      AND src.component_id != dst.component_id
                    GROUP BY src.component_id, dst.component_id, e.edge_type
                    ORDER BY edge_count DESC, source, target, e.edge_type
                    LIMIT ?
                    """,
                    [component_ids, component_ids, edge_limit + 1],
                )
                if component_ids and edge_limit
                else []
            )
        finally:
            db.close()
        return GraphView(
            level="component",
            nodes=tuple(_component_node(row) for row in selected),
            edges=tuple(_component_edge(row) for row in edge_rows[:edge_limit]),
            truncated_nodes=len(node_rows) > node_limit,
            truncated_edges=len(edge_rows) > edge_limit,
            coverage=self.coverage(),
        )

    def _db(self) -> DuckDB:
        return DuckDB(self.database_path, read_only=True)

    def _read_json(self, name: str) -> dict[str, object]:
        path = self.out_dir / name
        if not path.is_file():
            raise ValueError(f"Missing explorer artifact {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Explorer artifact {name} must be an object")
        return {str(key): item for key, item in value.items()}


def _event_filter(filters: GraphFilter) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if filters.domains:
        clauses.append("primary_domain IN (SELECT unnest(?))")
        params.append(list(filters.domains))
    if filters.active_only:
        clauses.append("active_market_count > 0")
    if filters.closed_only:
        clauses.append("closed_market_count > 0")
    return clauses, params


def _edge_filter(filters: GraphFilter, alias: str) -> tuple[str, list[object]]:
    clauses = [f"{alias}.confidence >= ?"]
    params: list[object] = [filters.min_confidence]
    if filters.relations:
        clauses.append(f"{alias}.edge_type IN (SELECT unnest(?))")
        params.append(list(filters.relations))
    elif not filters.include_compatible:
        clauses.append(f"{alias}.edge_type != 'compatible'")
    if filters.discovery_methods:
        clauses.append(f"{alias}.discovery_method IN (SELECT unnest(?))")
        params.append(list(filters.discovery_methods))
    if filters.evidence_tiers:
        clauses.append(f"{alias}.evidence_tier IN (SELECT unnest(?))")
        params.append(list(filters.evidence_tiers))
    return " AND " + " AND ".join(clauses), params


def _aggregate_edge_filter(filters: GraphFilter) -> tuple[str, list[object]]:
    clauses = ["mean_confidence >= ?"]
    params: list[object] = [filters.min_confidence]
    if filters.relations:
        clauses.append("edge_type IN (SELECT unnest(?))")
        params.append(list(filters.relations))
    elif not filters.include_compatible:
        clauses.append("edge_type != 'compatible'")
    if filters.evidence_tiers:
        clauses.append("evidence_tier IN (SELECT unnest(?))")
        params.append(list(filters.evidence_tiers))
    return " AND " + " AND ".join(clauses), params


def _event_node(row: dict[str, object]) -> ExplorerNode:
    propositions = _int(row["proposition_count"])
    return ExplorerNode(
        id=str(row["event_key"]),
        label=str(row["label"]),
        level="event",
        parent_id=str(row["component_id"]),
        x=_float(row["x"]),
        y=_float(row["y"]),
        size=_float(row["radius"]),
        domain=str(row["primary_domain"]),
        component_id=str(row["component_id"]),
        proposition_count=propositions,
        edge_count=_int(row["accepted_edge_count"]),
        classification_coverage=_float(row["classification_coverage"]),
    )


def _component_node(row: dict[str, object]) -> ExplorerNode:
    component_id = str(row["component_id"])
    return ExplorerNode(
        id=component_id,
        label=f"Component {component_id.removeprefix('component-')[:8]}",
        level="component",
        x=_float(row["x"]),
        y=_float(row["y"]),
        size=_float(row["radius"]),
        component_id=component_id,
        proposition_count=_int(row["proposition_count"]),
        edge_count=_int(row["edge_count"]),
        classification_coverage=_float(row["classification_coverage"]),
    )


def _proposition_node(row: dict[str, object]) -> ExplorerNode:
    node_id = str(row["node_id"])
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    angle = int.from_bytes(digest[:4], "big") / (2**32) * 2 * math.pi
    radius = 40.0 + int.from_bytes(digest[4:8], "big") / (2**32) * 180.0
    return ExplorerNode(
        id=node_id,
        label=str(row["canonical_proposition"]),
        level="proposition",
        parent_id=str(row["event_key"]),
        x=math.cos(angle) * radius,
        y=math.sin(angle) * radius,
        size=max(4.0, math.sqrt(_int(row["total_degree"]) + 1) * 3.0),
        component_id=str(row["component_id"]),
        market_id=str(row["market_id"]),
        edge_count=_int(row["total_degree"]),
        classification_coverage=_float(row["classification_coverage"]),
    )


def _event_edge(row: dict[str, object]) -> ExplorerEdge:
    source = str(row["src_event_key"])
    target = str(row["dst_event_key"])
    relation = cast(str, row["edge_type"])
    identity = json.dumps(
        [source, target, relation, row.get("evidence_tier")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ExplorerEdge.model_validate(
        {
            "id": f"event:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
            "source": source,
            "target": target,
            "relation": relation,
            "count": _int(row["edge_count"]),
            "confidence": _float(row["mean_confidence"]),
            "discovery_method": "aggregate",
            "evidence_tier": str(row.get("evidence_tier") or "aggregate"),
            "aggregation_only": True,
        }
    )


def _component_edge(row: dict[str, object]) -> ExplorerEdge:
    source = str(row["source"])
    target = str(row["target"])
    relation = cast(str, row["edge_type"])
    return ExplorerEdge.model_validate(
        {
            "id": f"component:{source}:{target}:{relation}",
            "source": source,
            "target": target,
            "relation": relation,
            "count": _int(row["edge_count"]),
            "confidence": _float(row["confidence"]),
            "discovery_method": "aggregate",
            "evidence_tier": "aggregate",
            "aggregation_only": True,
        }
    )


def _logic_edge(row: dict[str, object]) -> ExplorerEdge:
    return ExplorerEdge.model_validate(
        {
            "id": str(row["proposal_id"]),
            "source": str(row["src_node_id"]),
            "target": str(row["dst_node_id"]),
            "relation": str(row["edge_type"]),
            "count": 1,
            "confidence": _float(row["confidence"]),
            "discovery_method": str(row["discovery_method"]),
            "evidence_tier": str(row.get("evidence_tier") or "deterministic_rule"),
            "aggregation_only": False,
        }
    )


def _select_recording_highlights(
    candidates: list[dict[str, object]],
    limit: int,
) -> list[tuple[dict[str, object], RecordingScoreBreakdown]]:
    remaining = list(candidates)
    selected: list[tuple[dict[str, object], RecordingScoreBreakdown]] = []
    while remaining and len(selected) < limit:
        scored = [
            (row, _recording_score(row, [item[0] for item in selected]))
            for row in remaining
        ]
        winner = min(
            scored,
            key=lambda item: (
                -item[1].selection_score,
                str(item[0]["proposal_id"]),
            ),
        )
        selected.append(winner)
        winner_id = str(winner[0]["proposal_id"])
        remaining = [
            row for row in remaining if str(row["proposal_id"]) != winner_id
        ]
    return selected


def _recording_score(
    row: dict[str, object],
    selected: list[dict[str, object]],
) -> RecordingScoreBreakdown:
    relation = str(row["edge_type"])
    evidence_tier = str(row["evidence_tier"])
    event_pair = frozenset(
        (str(row["src_event_key"]), str(row["dst_event_key"]))
    )
    endpoints = frozenset(
        (str(row["src_node_id"]), str(row["dst_node_id"]))
    )
    same_relation_count = sum(
        str(other["edge_type"]) == relation for other in selected
    )
    same_evidence_tier_count = sum(
        str(other["evidence_tier"]) == evidence_tier for other in selected
    )
    same_event_pair_count = sum(
        frozenset(
            (str(other["src_event_key"]), str(other["dst_event_key"]))
        )
        == event_pair
        for other in selected
    )
    shared_endpoint_count = sum(
        len(
            endpoints
            & frozenset(
                (str(other["src_node_id"]), str(other["dst_node_id"]))
            )
        )
        for other in selected
    )
    confidence = _float(row["confidence"])
    scope = _float(row["scope"])
    structural_reach = _float(row["structural_reach"])
    evidence_interest = _float(row["evidence_interest"])
    relation_interest = _float(row["relation_interest"])
    confidence_contribution = 0.30 * confidence
    scope_contribution = 0.25 * scope
    structural_reach_contribution = 0.20 * structural_reach
    evidence_interest_contribution = 0.15 * evidence_interest
    relation_interest_contribution = 0.10 * relation_interest
    base_importance = (
        confidence_contribution
        + scope_contribution
        + structural_reach_contribution
        + evidence_interest_contribution
        + relation_interest_contribution
    )
    same_relation_penalty = 0.08 * same_relation_count
    same_evidence_tier_penalty = 0.10 * same_evidence_tier_count
    same_event_pair_penalty = 0.15 * same_event_pair_count
    shared_endpoint_penalty = 0.20 * shared_endpoint_count
    total_penalty = (
        same_relation_penalty
        + same_evidence_tier_penalty
        + same_event_pair_penalty
        + shared_endpoint_penalty
    )
    return RecordingScoreBreakdown(
        confidence=confidence,
        scope=scope,
        structural_reach=structural_reach,
        evidence_interest=evidence_interest,
        relation_interest=relation_interest,
        confidence_contribution=confidence_contribution,
        scope_contribution=scope_contribution,
        structural_reach_contribution=structural_reach_contribution,
        evidence_interest_contribution=evidence_interest_contribution,
        relation_interest_contribution=relation_interest_contribution,
        base_importance=base_importance,
        same_relation_count=same_relation_count,
        same_evidence_tier_count=same_evidence_tier_count,
        same_event_pair_count=same_event_pair_count,
        shared_endpoint_count=shared_endpoint_count,
        same_relation_penalty=same_relation_penalty,
        same_evidence_tier_penalty=same_evidence_tier_penalty,
        same_event_pair_penalty=same_event_pair_penalty,
        shared_endpoint_penalty=shared_endpoint_penalty,
        total_penalty=total_penalty,
        selection_score=base_importance - total_penalty,
    )


def _recording_highlight(
    row: dict[str, object],
    breakdown: RecordingScoreBreakdown,
    *,
    rank: int,
) -> RecordingHighlight:
    explanation = " ".join(
        str(row.get("explanation") or row.get("evidence") or "").split()
    )[:180]
    return RecordingHighlight.model_validate(
        {
            "rank": rank,
            "proposal_id": str(row["proposal_id"]),
            "source_id": str(row["src_node_id"]),
            "source_label": str(row["src_label"]),
            "source_market_id": str(row["src_market_id"]),
            "source_event_key": str(row["src_event_key"]),
            "source_domain": str(row["src_domain"]),
            "target_id": str(row["dst_node_id"]),
            "target_label": str(row["dst_label"]),
            "target_market_id": str(row["dst_market_id"]),
            "target_event_key": str(row["dst_event_key"]),
            "target_domain": str(row["dst_domain"]),
            "relation": str(row["edge_type"]),
            "confidence": _float(row["confidence"]),
            "evidence_tier": str(row["evidence_tier"]),
            "discovery_method": str(row["discovery_method"]),
            "explanation_excerpt": explanation,
            "importance_score": breakdown.base_importance,
            "score_breakdown": breakdown,
        }
    )


def _prune_recording_context(
    edge_by_id: dict[str, dict[str, object]],
    selected_ids: frozenset[str],
    *,
    max_nodes: int,
    max_edges: int,
) -> tuple[list[dict[str, object]], set[str]]:
    selected_edges = [edge_by_id[proposal_id] for proposal_id in selected_ids]
    selected_edges.sort(key=lambda row: str(row["proposal_id"]))
    retained = list(selected_edges)
    retained_ids = set(selected_ids)
    retained_nodes = {
        str(row[key])
        for row in selected_edges
        for key in ("src_node_id", "dst_node_id")
    }
    if len(retained) > max_edges or len(retained_nodes) > max_nodes:
        raise ValueError("Selected recording highlights exceed context bounds")
    context_edges = sorted(
        (
            row
            for proposal_id, row in edge_by_id.items()
            if proposal_id not in selected_ids
        ),
        key=lambda row: (
            -_float(row["confidence"]),
            str(row["edge_type"]),
            str(row["proposal_id"]),
        ),
    )
    for row in context_edges:
        proposal_id = str(row["proposal_id"])
        nodes = {str(row["src_node_id"]), str(row["dst_node_id"])}
        if len(retained_ids) >= max_edges:
            break
        if len(retained_nodes | nodes) > max_nodes:
            continue
        retained.append(row)
        retained_ids.add(proposal_id)
        retained_nodes.update(nodes)
    return retained, retained_nodes


def _bounded(value: int, minimum: int, maximum: int, name: str) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _int(value: object) -> int:
    return int(cast(int, value))


def _float(value: object) -> float:
    return float(cast(float, value))
