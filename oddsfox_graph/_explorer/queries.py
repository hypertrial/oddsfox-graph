"""Bounded, parameterized queries for completed graph outputs."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal, cast

from .contracts import (
    ClaimSummary,
    CompareResult,
    ComponentDetail,
    ComponentSummary,
    CoverageStatus,
    EdgeMode,
    EntitySearchResult,
    EventDetail,
    EventMarketSummary,
    EventSummary,
    ExploreHome,
    ExplorerEdge,
    ExplorerMetadata,
    ExplorerNode,
    GraphFilter,
    GraphPage,
    GraphView,
    HumanHighlight,
    LayoutMode,
    MarketDetail,
    RelationshipDetail,
    RecordingContextPruning,
    RecordingHighlight,
    RecordingPlan,
    RecordingScoreBreakdown,
    QuarantineSummary,
    StageDetail,
    StageSummary,
    TeamDetail,
    TeamSummary,
)
from .derived import essential_relationship_rows, graph_display_stats
from .human import HumanExplorer
from .. import __version__
from .._discovery.versions import WC2026_SOURCE_SCHEMA
from .._discovery.manifest_contracts import (
    CoverageSummary,
    load_build_manifest,
    load_viewer_manifest,
)
from ..queries import DuckDB


_RECORDING_CANDIDATES_SQL = """
WITH qualified AS (
    SELECT
        e.*,
        src_node.canonical_proposition AS src_label,
        src.market_id AS src_market_id,
        src.event_key AS src_event_key,
        coalesce(src.primary_domain, 'sports') AS src_domain,
        src.team_name AS src_team_name,
        src.stage_key AS src_stage_key,
        src.stage_rank AS src_stage_rank,
        src.progression_level AS src_progression_level,
        src.is_progression AS src_is_progression,
        src.component_id AS component_id,
        dst_node.canonical_proposition AS dst_label,
        dst.market_id AS dst_market_id,
        dst.event_key AS dst_event_key,
        coalesce(dst.primary_domain, 'sports') AS dst_domain,
        dst.team_name AS dst_team_name,
        dst.stage_key AS dst_stage_key,
        dst.stage_rank AS dst_stage_rank,
        dst.progression_level AS dst_progression_level,
        dst.is_progression AS dst_is_progression,
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
), contextual AS (
    SELECT *,
        concat_ws(':', src_progression_level, dst_progression_level,
                  src_is_progression, dst_is_progression) AS template_key,
        nullif(trim(coalesce(explanation, evidence, '')), '') IS NOT NULL
          AND nullif(trim(src_team_name), '') IS NOT NULL
          AND nullif(trim(dst_team_name), '') IS NOT NULL
          AND src_stage_rank BETWEEN 0 AND 5
          AND dst_stage_rank BETWEEN 0 AND 5 AS has_human_context
    FROM deduplicated
), eligible AS (
    SELECT *, count(*) OVER () AS eligible_edge_count
    FROM contextual
    WHERE has_human_context
)
SELECT *
FROM eligible
ORDER BY confidence DESC, proposal_id
LIMIT ?
"""

_PLOT_COLUMN_SPACING = 260.0
_PLOT_POLARITY_X_OFFSET = 42.0
_PLOT_MARKET_LANE_SPACING = 48.0
_PLOT_MIN_TEAM_ROW_SPACING = 96.0


_RECORDING_EXCLUSIONS_SQL = """
WITH qualified AS (
    SELECT e.*, src.team_name AS src_team_name,
           src.stage_rank AS src_stage_rank,
           dst.team_name AS dst_team_name,
           dst.stage_rank AS dst_stage_rank,
           row_number() OVER (
            PARTITION BY e.edge_type,
                CASE WHEN e.edge_type = 'implies' THEN e.src_node_id
                     ELSE least(e.src_node_id, e.dst_node_id) END,
                CASE WHEN e.edge_type = 'implies' THEN e.dst_node_id
                     ELSE greatest(e.src_node_id, e.dst_node_id) END
            ORDER BY e.proposal_id
           ) AS duplicate_rank
    FROM logic_edges_v e
    JOIN explorer_propositions_v src ON src.proposition_id = e.src_node_id
    JOIN explorer_propositions_v dst ON dst.proposition_id = e.dst_node_id
    WHERE e.edge_type != 'compatible' AND e.confidence >= ?
)
SELECT count(*) FILTER (
    WHERE duplicate_rank = 1 AND NOT (
        nullif(trim(coalesce(explanation, evidence, '')), '') IS NOT NULL
        AND nullif(trim(src_team_name), '') IS NOT NULL
        AND nullif(trim(dst_team_name), '') IS NOT NULL
        AND src_stage_rank BETWEEN 0 AND 5
        AND dst_stage_rank BETWEEN 0 AND 5
    )
)::BIGINT AS missing_context
FROM qualified
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
            viewer=load_viewer_manifest(self.out_dir / "viewer_manifest.json"),
            coverage=self.coverage(),
            build=load_build_manifest(self.out_dir / "build_manifest.json"),
        )

    def coverage(self) -> CoverageSummary:
        path = self.out_dir / "coverage_summary.json"
        try:
            return CoverageSummary.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Graph coverage summary is incompatible; run a clean v0.13 "
                "WC2026 discovery"
            ) from exc

    def explore_home(
        self,
        *,
        team_limit: int = 24,
        highlight_limit: int = 6,
    ) -> ExploreHome:
        db = self._db()
        try:
            return self._human(db).explore_home(
                team_limit=team_limit,
                highlight_limit=highlight_limit,
            )
        finally:
            db.close()

    def stages(self) -> tuple[StageSummary, ...]:
        db = self._db()
        try:
            return self._human(db).stages()
        finally:
            db.close()

    def stage(self, stage_key: str) -> StageDetail:
        db = self._db()
        try:
            return self._human(db).stage(stage_key)
        finally:
            db.close()

    def teams(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[TeamSummary]:
        db = self._db()
        try:
            rows, next_cursor, truncated = self._human(db).teams(
                cursor=cursor, limit=limit
            )
            return GraphPage[TeamSummary](
                rows=rows,
                next_cursor=next_cursor,
                truncated=truncated,
            )
        finally:
            db.close()

    def team(self, team_key: str) -> TeamDetail:
        db = self._db()
        try:
            return self._human(db).team(team_key)
        finally:
            db.close()

    def market(self, market_id: str) -> MarketDetail:
        db = self._db()
        try:
            return self._human(db).market(market_id)
        finally:
            db.close()

    def relationship(self, proposal_id: str) -> RelationshipDetail:
        db = self._db()
        try:
            return self._human(db).relationship(proposal_id)
        finally:
            db.close()

    def human_highlights(
        self,
        *,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> tuple[HumanHighlight, ...]:
        db = self._db()
        try:
            return self._human(db).highlights(
                limit=limit, min_confidence=min_confidence
            )
        finally:
            db.close()

    def entity_search(
        self, query: str, *, limit: int = 20
    ) -> tuple[EntitySearchResult, ...]:
        db = self._db()
        try:
            return self._human(db).search(query, limit=limit)
        finally:
            db.close()

    def claim_search(
        self, query: str, *, limit: int = 20
    ) -> tuple[ClaimSummary, ...]:
        db = self._db()
        try:
            return self._human(db).search_claims(query, limit=limit)
        finally:
            db.close()

    def plain_claims(
        self, node_ids: tuple[str, ...]
    ) -> dict[str, str | None]:
        if not node_ids:
            return {}
        db = self._db()
        try:
            rows = db.rows(
                """
                SELECT n.node_id, p.team_name, p.progression_level,
                       p.is_progression
                FROM nodes_table n
                LEFT JOIN explorer_propositions_v p
                  ON p.proposition_id = n.node_id
                WHERE n.node_id IN (SELECT unnest(?))
                ORDER BY n.node_id
                """,
                [list(node_ids)],
            )
        finally:
            db.close()
        return {
            str(row["node_id"]): (
                _recording_plain_claim(
                    str(row["team_name"]),
                    _int(row["progression_level"]),
                    bool(row["is_progression"]),
                )
                if row.get("team_name")
                and row.get("progression_level") is not None
                and row.get("is_progression") is not None
                else None
            )
            for row in rows
        }

    def compare(
        self, source_id: str, target_id: str, *, max_hops: int = 4
    ) -> CompareResult:
        db = self._db()
        try:
            return self._human(db).compare(
                source_id, target_id, max_hops=max_hops
            )
        finally:
            db.close()

    def events(
        self,
        filters: GraphFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[EventSummary]:
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
        return GraphPage[EventSummary](
            rows=tuple(EventSummary.model_validate(row) for row in selected),
            next_cursor=(str(selected[-1]["event_key"]) if truncated else None),
            truncated=truncated,
        )

    def components(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[ComponentSummary]:
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
        return GraphPage[ComponentSummary](
            rows=tuple(ComponentSummary.model_validate(row) for row in selected),
            next_cursor=(str(selected[-1]["component_id"]) if truncated else None),
            truncated=truncated,
        )

    def event(self, event_key: str) -> EventDetail:
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
            return EventDetail(
                summary=EventSummary.model_validate(summary[0]),
                markets=tuple(
                    EventMarketSummary.model_validate(row)
                    for row in markets[:1_000]
                ),
                markets_truncated=len(markets) > 1_000,
            )
        finally:
            db.close()

    def component(self, component_id: str) -> ComponentDetail:
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
            return ComponentDetail(
                summary=ComponentSummary.model_validate(summary[0]),
                event_keys=tuple(
                    str(row["event_key"]) for row in events[:1_000]
                ),
                events_truncated=len(events) > 1_000,
            )
        finally:
            db.close()

    def overview(
        self,
        level: Literal["component", "event", "proposition"] = "event",
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        node_limit = _bounded(max_nodes, 1, 5_000, "node limit")
        edge_limit = _bounded(max_edges, 0, 10_000, "edge limit")
        if level == "proposition":
            return self._proposition_overview(
                filters or GraphFilter(),
                node_limit,
                edge_limit,
                edge_mode=edge_mode,
            )
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
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        if not node_ids:
            raise ValueError("At least one seed node is required")
        if edge_mode not in {"all", "essential"}:
            raise ValueError("edge_mode must be all or essential")
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
                       m.classification_state, m.classification_status,
                       m.classification_coverage,
                       p.event_key, p.team_name, p.stage_key,
                       p.progression_level,
                       p.is_progression,
                       epoch(p.market_close_time)::BIGINT AS market_close_epoch
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
        nodes, layout_mode = _proposition_nodes(node_rows)
        edge_rows = sorted(
            edge_by_id.values(),
            key=lambda row: (
                -float(cast(float, row["confidence"])),
                str(row["edge_type"]),
                str(row["src_node_id"]),
                str(row["dst_node_id"]),
            ),
        )
        input_edge_count = len(edge_rows[:edge_limit])
        if edge_mode == "essential":
            edge_rows = essential_relationship_rows(edge_rows[:edge_limit])
        else:
            edge_rows = edge_rows[:edge_limit]
        edges = tuple(_logic_edge(row) for row in edge_rows)
        labels = tuple(node.label for node in nodes)
        return GraphView(
            level="proposition",
            nodes=nodes,
            edges=edges,
            truncated_nodes=truncated_nodes,
            truncated_edges=truncated_edges,
            coverage=self.coverage(),
            edge_mode=edge_mode,
            layout_mode=layout_mode,
            display_stats=graph_display_stats(
                labels,
                tuple((edge.source, edge.target) for edge in edges),
                input_edge_count=input_edge_count,
            ),
        )

    def _proposition_overview(
        self,
        filters: GraphFilter,
        node_limit: int,
        edge_limit: int,
        *,
        edge_mode: EdgeMode,
    ) -> GraphView:
        if edge_mode not in {"all", "essential"}:
            raise ValueError("edge_mode must be all or essential")
        node_where, node_params = _proposition_filter(filters)
        sql_where = "WHERE " + " AND ".join(node_where) if node_where else ""
        edge_sql, edge_params = _edge_filter(filters, "e")
        db = self._db()
        try:
            node_rows = db.rows(
                f"""
                SELECT n.*, m.component_id, m.total_degree,
                       m.classification_state, m.classification_status,
                       m.classification_coverage,
                       p.event_key, p.team_name, p.stage_key,
                       p.progression_level,
                       p.is_progression,
                       epoch(p.market_close_time)::BIGINT AS market_close_epoch
                FROM nodes_table n
                JOIN node_metrics_v m USING (node_id)
                JOIN explorer_propositions_v p ON p.proposition_id = n.node_id
                {sql_where}
                ORDER BY p.market_close_time NULLS LAST, p.team_name,
                         p.market_id, p.is_progression DESC, n.node_id
                LIMIT ?
                """,
                [*node_params, node_limit + 1],
            )
            selected = node_rows[:node_limit]
            node_ids = [str(row["node_id"]) for row in selected]
            edge_rows = (
                db.rows(
                    f"""
                    SELECT e.*
                    FROM logic_edges_v e
                    WHERE e.src_node_id IN (SELECT unnest(?))
                      AND e.dst_node_id IN (SELECT unnest(?))
                      {edge_sql}
                    ORDER BY e.confidence DESC, e.edge_type,
                             e.src_node_id, e.dst_node_id, e.proposal_id
                    LIMIT ?
                    """,
                    [node_ids, node_ids, *edge_params, edge_limit + 1],
                )
                if node_ids
                else []
            )
        finally:
            db.close()
        input_edge_count = min(len(edge_rows), edge_limit)
        selected_edges = edge_rows[:edge_limit]
        if edge_mode == "essential":
            selected_edges = essential_relationship_rows(selected_edges)
        nodes, layout_mode = _proposition_nodes(selected)
        edges = tuple(_logic_edge(row) for row in selected_edges)
        return GraphView(
            level="proposition",
            nodes=nodes,
            edges=edges,
            truncated_nodes=len(node_rows) > node_limit,
            truncated_edges=len(edge_rows) > edge_limit,
            coverage=self.coverage(),
            edge_mode=edge_mode,
            layout_mode=layout_mode,
            display_stats=graph_display_stats(
                tuple(node.label for node in nodes),
                tuple((edge.source, edge.target) for edge in edges),
                input_edge_count=input_edge_count,
            ),
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
        edge_mode: EdgeMode = "all",
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
            edge_mode=edge_mode,
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
    ) -> GraphPage[QuarantineSummary]:
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
        return GraphPage[QuarantineSummary](
            rows=tuple(QuarantineSummary.model_validate(row) for row in selected),
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
            self._human(db)
            raw_candidates = db.rows(
                _RECORDING_CANDIDATES_SQL,
                [min_confidence, pool_limit],
            )
            exclusions = db.rows(
                _RECORDING_EXCLUSIONS_SQL,
                [min_confidence],
            )[0]
            if not raw_candidates:
                raise ValueError(
                    "No accepted non-compatible logical edges meet the "
                    f"{min_confidence:.3f} recording confidence threshold "
                    "with complete World Cup human context"
                )
            candidates = _prepare_recording_candidates(
                essential_relationship_rows(raw_candidates)
            )
            selected = _select_recording_highlights(candidates, bounded_limit)
            if not selected:
                raise ValueError(
                    "No diverse World Cup recording highlight satisfies the "
                    "human-context and selection constraints"
                )
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
                    2,
                ],
            )
            selected_by_id = {
                str(row["proposal_id"]): row for row, _ in selected
            }
            context_by_id = {
                str(row["proposal_id"]): row for row in context_rows
            }
            context_by_id.update(selected_by_id)
            context_by_id = {
                str(row["proposal_id"]): row
                for row in essential_relationship_rows(list(context_by_id.values()))
            }
            context_by_id.update(selected_by_id)
            retained_edges, retained_nodes = _prune_recording_context(
                context_by_id,
                frozenset(selected_by_id),
                max_nodes=96,
                max_edges=144,
            )
            node_rows = db.rows(
                """
                SELECT n.*, m.component_id, m.total_degree,
                       m.classification_state, m.classification_status,
                       m.classification_coverage,
                       p.event_key, p.team_name, p.stage_key,
                       p.progression_level,
                       p.is_progression,
                       epoch(p.market_close_time)::BIGINT AS market_close_epoch
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
        graph_fingerprint = viewer.graph_content_fingerprint
        mode = viewer.build_mode
        if mode not in {"fast", "full"}:
            raise ValueError("Graph viewer manifest has no valid build mode")
        validation_status = viewer.validation_status
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
        eligible_count = _int(raw_candidates[0]["eligible_edge_count"])
        selected_pathological = sum(
            bool(row.get("pathological")) for row, _ in selected
        )
        pathological_candidates = sum(
            bool(row.get("pathological")) for row in candidates
        )
        return RecordingPlan(
            graph_fingerprint=graph_fingerprint,
            mode=mode,
            validation_status=validation_status,
            requested_limit=bounded_limit,
            min_confidence=min_confidence,
            eligible_edge_count=eligible_count,
            candidate_pool_size=len(candidates),
            excluded_missing_context=_int(exclusions["missing_context"]),
            excluded_pathological=max(
                0, pathological_candidates - selected_pathological
            ),
            highlights=highlights,
            graph=view,
            context_pruning=RecordingContextPruning(
                incident_edge_cap_per_endpoint=2,
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
                        e.evidence_tier,
                        count(*)::BIGINT AS edge_count,
                        avg(e.confidence)::DOUBLE AS confidence
                    FROM logic_edges_v e
                    JOIN node_components_v src ON src.node_id = e.src_node_id
                    JOIN node_components_v dst ON dst.node_id = e.dst_node_id
                    WHERE src.component_id IN (SELECT unnest(?))
                      AND dst.component_id IN (SELECT unnest(?))
                      AND src.component_id != dst.component_id
                    GROUP BY src.component_id, dst.component_id, e.edge_type,
                             e.evidence_tier
                    ORDER BY edge_count DESC, source, target, e.edge_type,
                             e.evidence_tier
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

    def _human(self, db: DuckDB) -> HumanExplorer:
        metadata = self.metadata()
        input_profile = metadata.build.input.schema
        if input_profile != WC2026_SOURCE_SCHEMA:
            raise ValueError(
                "World Cup exploration and recording require a graph built "
                "with --input-profile polymarket-wc2026-graph-hourly-v1"
            )
        return HumanExplorer(
            db,
            coverage=metadata.coverage,
            build=metadata.build,
        )

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


def _proposition_filter(filters: GraphFilter) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if filters.domains:
        clauses.append("p.primary_domain IN (SELECT unnest(?))")
        params.append(list(filters.domains))
    if filters.active_only:
        clauses.append("n.is_active")
    if filters.closed_only:
        clauses.append("n.is_closed")
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
        classification_coverage=_optional_float(row.get("classification_coverage")),
        classification_status=_coverage_status(row),
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
        classification_coverage=_optional_float(row.get("classification_coverage")),
        classification_status=_coverage_status(row),
    )


def _proposition_node(row: dict[str, object]) -> ExplorerNode:
    node_id = str(row["node_id"])
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    angle = int.from_bytes(digest[:4], "big") / (2**32) * 2 * math.pi
    radius = 40.0 + int.from_bytes(digest[4:8], "big") / (2**32) * 180.0
    close_epoch = row.get("market_close_epoch")
    progression = row.get("is_progression")
    team = row.get("team_name")
    level = row.get("progression_level")
    stage_key = row.get("stage_key")
    label = (
        _recording_plain_claim(str(team), _int(level), bool(progression))
        if team and level is not None and progression is not None
        else str(row["canonical_proposition"])
    )
    return ExplorerNode(
        id=node_id,
        label=label,
        level="proposition",
        parent_id=str(row["event_key"]),
        x=_float(row.get("layout_x", math.cos(angle) * radius)),
        y=_float(row.get("layout_y", math.sin(angle) * radius)),
        size=max(4.0, math.sqrt(_int(row["total_degree"]) + 1) * 3.0),
        domain=(str(team) if team else None),
        component_id=str(row["component_id"]),
        market_id=str(row["market_id"]),
        edge_count=_int(row["total_degree"]),
        classification_coverage=_optional_float(row.get("classification_coverage")),
        classification_status=_coverage_status(row),
        progression_outcome=(None if progression is None else bool(progression)),
        progression_level=(None if level is None else _int(level)),
        stage_key=(None if stage_key is None else str(stage_key)),
        market_close_epoch=(None if close_epoch is None else _int(close_epoch)),
    )


def _proposition_nodes(
    rows: list[dict[str, object]],
) -> tuple[tuple[ExplorerNode, ...], LayoutMode]:
    if not rows:
        return tuple(_proposition_node(row) for row in rows), "hierarchical"

    has_teams = all(row.get("team_name") for row in rows)
    has_close_times = has_teams and all(
        row.get("market_close_epoch") is not None for row in rows
    )
    has_progression_semantics = has_teams and all(
        row.get("progression_level") is not None
        and row.get("is_progression") is not None
        for row in rows
    )
    if has_close_times:
        close_epochs = sorted({_int(row["market_close_epoch"]) for row in rows})
        columns = {
            epoch: index * _PLOT_COLUMN_SPACING
            for index, epoch in enumerate(close_epochs)
        }
        column_key = "market_close_epoch"
        layout_mode: LayoutMode = "close_time"
    elif has_progression_semantics:
        progression_levels = sorted({_int(row["progression_level"]) for row in rows})
        columns = {
            level: level * _PLOT_COLUMN_SPACING for level in progression_levels
        }
        column_key = "progression_level"
        layout_mode = "progression"
    else:
        return tuple(_proposition_node(row) for row in rows), "hierarchical"

    teams = sorted({str(row["team_name"]) for row in rows})
    team_rows = {team: index for index, team in enumerate(teams)}
    grouped_markets: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        key = (str(row["team_name"]), _int(row[column_key]))
        grouped_markets.setdefault(key, set()).add(str(row["market_id"]))
    market_offsets: dict[str, float] = {}
    for markets_set in grouped_markets.values():
        markets = sorted(markets_set)
        for index, market_id in enumerate(markets):
            market_offsets[market_id] = index - (len(markets) - 1) / 2
    max_parallel_markets = max(len(markets) for markets in grouped_markets.values())
    team_row_spacing = max(
        _PLOT_MIN_TEAM_ROW_SPACING,
        max_parallel_markets * _PLOT_MARKET_LANE_SPACING,
    )
    positioned = []
    for row in rows:
        team = str(row["team_name"])
        progression = row.get("is_progression")
        polarity_offset = (
            -_PLOT_POLARITY_X_OFFSET
            if progression is True
            else _PLOT_POLARITY_X_OFFSET
            if progression is False
            else 0.0
        )
        positioned.append(
            {
                **row,
                "layout_x": columns[_int(row[column_key])] + polarity_offset,
                "layout_y": team_rows[team] * team_row_spacing
                + market_offsets[str(row["market_id"])]
                * _PLOT_MARKET_LANE_SPACING,
            }
        )
    return tuple(_proposition_node(row) for row in positioned), layout_mode


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
            "evidence_tier": str(
                row.get("evidence_tier") or "deterministic_rule"
            ),
            "aggregation_only": True,
        }
    )


def _component_edge(row: dict[str, object]) -> ExplorerEdge:
    source = str(row["source"])
    target = str(row["target"])
    relation = cast(str, row["edge_type"])
    return ExplorerEdge.model_validate(
        {
            "id": (
                f"component:{source}:{target}:{relation}:"
                f"{row['evidence_tier']}"
            ),
            "source": source,
            "target": target,
            "relation": relation,
            "count": _int(row["edge_count"]),
            "confidence": _float(row["confidence"]),
            "discovery_method": "aggregate",
            "evidence_tier": str(row["evidence_tier"]),
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
        selected_rows = [item[0] for item in selected]
        used_teams = {
            str(item[key])
            for item in selected_rows
            for key in ("src_team_name", "dst_team_name")
        }
        used_templates = {str(item["template_key"]) for item in selected_rows}
        used_endpoints = {
            str(item[key])
            for item in selected_rows
            for key in ("src_node_id", "dst_node_id")
        }
        pathological_selected = sum(
            bool(item.get("pathological")) for item in selected_rows
        )
        admissible = [
            row
            for row in remaining
            if not (
                {str(row["src_team_name"]), str(row["dst_team_name"])}
                & used_teams
            )
            and str(row["template_key"]) not in used_templates
            and not (
                {str(row["src_node_id"]), str(row["dst_node_id"])}
                & used_endpoints
            )
            and not (bool(row.get("pathological")) and pathological_selected)
        ]
        if not admissible:
            break
        scored = [
            (row, _recording_score(row, selected_rows))
            for row in admissible
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


def _prepare_recording_candidates(
    candidates: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Compute versioned WC2026 ranking features from essential edges."""

    if not candidates:
        return []
    template_frequency: dict[str, int] = {}
    component_frequency: dict[str, int] = {}
    component_nodes: dict[str, set[str]] = {}
    component_templates: dict[str, set[str]] = {}
    component_relations: dict[str, dict[str, int]] = {}
    for row in candidates:
        template = str(row["template_key"])
        component = _recording_story_component(row)
        relation = str(row["edge_type"])
        template_frequency[template] = template_frequency.get(template, 0) + 1
        component_frequency[component] = component_frequency.get(component, 0) + 1
        component_nodes.setdefault(component, set()).update(
            (str(row["src_node_id"]), str(row["dst_node_id"]))
        )
        component_templates.setdefault(component, set()).add(template)
        relation_counts = component_relations.setdefault(component, {})
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    max_degree = max(_int(row["degree_sum"]) for row in candidates)
    prepared: list[dict[str, object]] = []
    for original in candidates:
        row = dict(original)
        component = _recording_story_component(row)
        count = component_frequency[component]
        node_count = len(component_nodes[component])
        component_edges = count
        density = (
            component_edges / (node_count * (node_count - 1))
            if node_count > 1
            else 0.0
        )
        template_uniqueness = len(component_templates[component]) / count
        relation_dominance = max(component_relations[component].values()) / count
        pathological = node_count >= 20 and (
            template_uniqueness < 0.15
            or density > 0.15
            or relation_dominance > 0.85
        )
        structural_reach = (
            math.log1p(_int(row["degree_sum"])) / math.log1p(max_degree)
            if max_degree > 0
            else 0.0
        )
        if pathological:
            structural_reach *= 0.5
        stage_importance = max(
            _int(row["src_progression_level"]),
            _int(row["dst_progression_level"]),
        ) / 5.0
        template_novelty = 1.0 / math.sqrt(
            template_frequency[str(row["template_key"])]
        )
        evidence_interest = {
            "generative_consensus": 1.0,
            "deterministic_rule": 0.80,
            "source_contract": 0.55,
        }.get(str(row["evidence_tier"]), 0.0)
        relation_interest = {
            "implies": 1.0,
            "equivalent": 0.90,
            "mutually_exclusive": 0.85,
            "complement": 0.60,
        }.get(str(row["edge_type"]), 0.0)
        row.update(
            {
                "stage_importance": stage_importance,
                "structural_reach": structural_reach,
                "template_novelty": template_novelty,
                "evidence_interest": evidence_interest,
                "relation_interest": relation_interest,
                "pathological": pathological,
                "story_component_id": component,
                "component_density": density,
                "component_template_uniqueness": template_uniqueness,
                "component_relation_dominance": relation_dominance,
            }
        )
        prepared.append(row)
    prepared.sort(
        key=lambda row: (
            -(
                0.25 * _float(row["confidence"])
                + 0.25 * _float(row["stage_importance"])
                + 0.20 * _float(row["structural_reach"])
                + 0.15 * _float(row["template_novelty"])
                + 0.10 * _float(row["evidence_interest"])
                + 0.05 * _float(row["relation_interest"])
            ),
            str(row["proposal_id"]),
        )
    )
    return prepared


def _recording_story_component(row: dict[str, object]) -> str:
    """Group edges by the human story they belong to, not graph connectivity.

    The tournament winner-exclusion clique intentionally joins every team into one
    canonical graph component.  Using that component for story diversity makes all
    same-team progression relationships look like one pathological hairball.  Keep
    the canonical component on public highlights, while ranking same-team logic per
    team and the cross-team winner constraint as one tournament-level story.
    """

    source_team = str(row["src_team_name"])
    target_team = str(row["dst_team_name"])
    if source_team == target_team:
        return f"team:{source_team.casefold()}"
    if (
        str(row["edge_type"]) == "mutually_exclusive"
        and _int(row["src_progression_level"]) == 5
        and _int(row["dst_progression_level"]) == 5
        and bool(row["src_is_progression"])
        and bool(row["dst_is_progression"])
    ):
        return "tournament:winner-exclusion"
    return f"cross-team:{row['component_id']}"


def _recording_score(
    row: dict[str, object],
    selected: list[dict[str, object]],
) -> RecordingScoreBreakdown:
    relation = str(row["edge_type"])
    evidence_tier = str(row["evidence_tier"])
    target_stage = str(row["dst_progression_level"])
    component = str(row["story_component_id"])
    same_relation_count = sum(
        str(other["edge_type"]) == relation for other in selected
    )
    same_evidence_tier_count = sum(
        str(other["evidence_tier"]) == evidence_tier for other in selected
    )
    same_target_stage_count = sum(
        str(other["dst_progression_level"]) == target_stage
        for other in selected
    )
    same_component_count = sum(
        str(other["story_component_id"]) == component for other in selected
    )
    confidence = _float(row["confidence"])
    stage_importance = _float(row["stage_importance"])
    structural_reach = _float(row["structural_reach"])
    template_novelty = _float(row["template_novelty"])
    evidence_interest = _float(row["evidence_interest"])
    relation_interest = _float(row["relation_interest"])
    confidence_contribution = 0.25 * confidence
    stage_importance_contribution = 0.25 * stage_importance
    structural_reach_contribution = 0.20 * structural_reach
    template_novelty_contribution = 0.15 * template_novelty
    evidence_interest_contribution = 0.10 * evidence_interest
    relation_interest_contribution = 0.05 * relation_interest
    base_importance = (
        confidence_contribution
        + stage_importance_contribution
        + structural_reach_contribution
        + template_novelty_contribution
        + evidence_interest_contribution
        + relation_interest_contribution
    )
    same_relation_penalty = 0.08 * same_relation_count
    same_evidence_tier_penalty = 0.04 * same_evidence_tier_count
    same_target_stage_penalty = 0.10 * same_target_stage_count
    same_component_penalty = 0.12 * same_component_count
    total_penalty = (
        same_relation_penalty
        + same_evidence_tier_penalty
        + same_target_stage_penalty
        + same_component_penalty
    )
    return RecordingScoreBreakdown(
        confidence=confidence,
        stage_importance=stage_importance,
        structural_reach=structural_reach,
        template_novelty=template_novelty,
        evidence_interest=evidence_interest,
        relation_interest=relation_interest,
        confidence_contribution=confidence_contribution,
        stage_importance_contribution=stage_importance_contribution,
        structural_reach_contribution=structural_reach_contribution,
        template_novelty_contribution=template_novelty_contribution,
        evidence_interest_contribution=evidence_interest_contribution,
        relation_interest_contribution=relation_interest_contribution,
        base_importance=base_importance,
        same_relation_count=same_relation_count,
        same_evidence_tier_count=same_evidence_tier_count,
        same_target_stage_count=same_target_stage_count,
        same_component_count=same_component_count,
        same_relation_penalty=same_relation_penalty,
        same_evidence_tier_penalty=same_evidence_tier_penalty,
        same_target_stage_penalty=same_target_stage_penalty,
        same_component_penalty=same_component_penalty,
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
            "source_team_name": str(row["src_team_name"]),
            "source_stage_key": str(row["src_stage_key"]),
            "source_stage_rank": _int(row["src_stage_rank"]),
            "source_plain_claim": _recording_plain_claim(
                str(row["src_team_name"]),
                _int(row["src_progression_level"]),
                bool(row["src_is_progression"]),
            ),
            "target_id": str(row["dst_node_id"]),
            "target_label": str(row["dst_label"]),
            "target_market_id": str(row["dst_market_id"]),
            "target_event_key": str(row["dst_event_key"]),
            "target_domain": str(row["dst_domain"]),
            "target_team_name": str(row["dst_team_name"]),
            "target_stage_key": str(row["dst_stage_key"]),
            "target_stage_rank": _int(row["dst_stage_rank"]),
            "target_plain_claim": _recording_plain_claim(
                str(row["dst_team_name"]),
                _int(row["dst_progression_level"]),
                bool(row["dst_is_progression"]),
            ),
            "template_key": str(row["template_key"]),
            "component_id": str(row["component_id"]),
            "relation": str(row["edge_type"]),
            "confidence": _float(row["confidence"]),
            "evidence_tier": str(row["evidence_tier"]),
            "discovery_method": str(row["discovery_method"]),
            "explanation_excerpt": explanation,
            "importance_score": breakdown.base_importance,
            "score_breakdown": breakdown,
        }
    )


def _recording_plain_claim(team: str, level: int, progression: bool) -> str:
    positive = {
        0: "reaches the round of 32",
        1: "reaches the round of 16",
        2: "reaches the quarterfinals",
        3: "reaches the semifinals",
        4: "reaches the final",
        5: "wins the World Cup",
    }.get(level)
    negative = {
        0: "reach the round of 32",
        1: "reach the round of 16",
        2: "reach the quarterfinals",
        3: "reach the semifinals",
        4: "reach the final",
        5: "win the World Cup",
    }.get(level)
    if positive is None or negative is None:
        raise ValueError(f"Invalid World Cup progression level {level}")
    return f"{team} {positive}" if progression else f"{team} does not {negative}"


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


def _optional_float(value: object) -> float | None:
    return None if value is None else float(cast(float, value))


def _coverage_status(row: dict[str, object]) -> CoverageStatus:
    explicit = row.get("classification_status")
    if explicit in {"not_applicable", "not_started", "partial", "complete"}:
        return cast(CoverageStatus, explicit)
    eligible = int(cast(int, row.get("classification_eligible_count") or 0))
    assessed = int(cast(int, row.get("classification_assessed_count") or 0))
    if eligible == 0:
        return "not_applicable"
    if assessed == 0:
        return "not_started"
    if assessed < eligible:
        return "partial"
    return "complete"
