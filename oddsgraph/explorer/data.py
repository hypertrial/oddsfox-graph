"""DuckDB query helpers for the local graph explorer."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from oddsgraph.bracket import tournament_playback_bounds, tournament_time_bounds
from oddsgraph.bracket_projection import home_prob_at_hour, split_match_teams
from oddsgraph.config import Settings
from oddsgraph.explorer import KNOCKOUT_STAGE_LABELS
from oddsgraph.explorer.presentation import (
    short_match_label,
    stage_rank,
)
from oddsgraph.export import EDGE_SCHEMA, NODE_SCHEMA, table_with_schema
from oddsgraph.reduce import quote_path


@dataclass
class GraphSlice:
    """A set of bracket node/edge elements for the tree UI."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def to_elements(self) -> list[dict[str, Any]]:
        return [*self.nodes, *self.edges]


def _evidence_count(row: dict[str, Any]) -> int:
    evidence = row.get("evidence_market_ids") or []
    return len(evidence) if isinstance(evidence, list) else 0


def node_element(row: dict[str, Any], *, bracket: bool = False) -> dict[str, Any]:
    """Convert a parquet node row into a bracket element.

    Elements carry ``evidence_count`` only — full ``evidence_market_ids``
    stay in parquet.

    When ``bracket`` is True, attach short card labels and stage metadata.
    """
    data: dict[str, Any] = {
        "id": row["canonical_id"],
        "label": row["label"],
        "type": row["type"],
        "confidence": row["confidence"],
        "aliases": row.get("aliases") or [],
        "evidence_count": _evidence_count(row),
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
    """Convert a parquet edge row into a bracket edge element.

    Elements omit full ``evidence_market_ids`` / ``evidence_text``.
    """
    edge_id = f"{row['source_id']}|{row['edge_type']}|{row['target_id']}"
    return {
        "data": {
            "id": edge_id,
            "source": row["source_id"],
            "target": row["target_id"],
            "edge_type": row["edge_type"],
            "label": row["edge_type"],
            "confidence": row["confidence"],
            "evidence_count": _evidence_count(row),
            "inference_method": row.get("inference_method") or "",
        },
        "classes": row["edge_type"],
    }


def _fetch_dicts(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    result = conn.execute(sql, params or [])
    columns = [desc[0] for desc in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _parquet_mtimes(
    nodes_path: Path,
    edges_path: Path,
    odds_path: Path | None = None,
    stage_odds_path: Path | None = None,
) -> tuple[float, float, float, float]:
    return (
        nodes_path.stat().st_mtime if nodes_path.exists() else -1.0,
        edges_path.stat().st_mtime if edges_path.exists() else -1.0,
        odds_path.stat().st_mtime if odds_path is not None and odds_path.exists() else -1.0,
        (
            stage_odds_path.stat().st_mtime
            if stage_odds_path is not None and stage_odds_path.exists()
            else -1.0
        ),
    )


def _load_odds_history_by_match(path: Path) -> dict[str, dict[str, Any]]:
    """Group odds-history rows by match_canonical_id and by team pair."""
    if not path.exists():
        return {}
    conn = duckdb.connect(database=":memory:")
    try:
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM read_parquet('{quote_path(path)}')
            ORDER BY match_canonical_id, odds_hour_epoch
            """,
        )
    finally:
        conn.close()

    by_match: dict[str, dict[str, Any]] = {}
    for row in rows:
        match_id = str(row["match_canonical_id"])
        entry = by_match.setdefault(
            match_id,
            {
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "match_start_epoch": row.get("match_start_epoch"),
                "match_end_epoch": row.get("match_end_epoch"),
                "winner_team": row.get("winner_team"),
                "odds_series": [],
            },
        )
        entry["odds_series"].append(
            {
                "h": int(row["odds_hour_epoch"]),
                "home": float(row["home_prob"]),
                "away": float(row["away_prob"]),
            }
        )
    return by_match


def _load_stage_odds_by_team(
    path: Path,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Group stage-odds rows as team -> stage_label -> [{h, p}, ...]."""
    if not path.exists():
        return {}
    conn = duckdb.connect(database=":memory:")
    try:
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT team, stage_label, odds_hour_epoch, reach_prob
            FROM read_parquet('{quote_path(path)}')
            ORDER BY team, stage_label, odds_hour_epoch
            """,
        )
    finally:
        conn.close()

    by_team: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        team = str(row["team"])
        stage = str(row["stage_label"])
        series = by_team.setdefault(team, {}).setdefault(stage, [])
        series.append({"h": int(row["odds_hour_epoch"]), "p": float(row["reach_prob"])})
    return by_team


def _odds_indexes(
    odds_by_match: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[frozenset[str], dict[str, Any]]]:
    by_teams: dict[frozenset[str], dict[str, Any]] = {}
    for entry in odds_by_match.values():
        home = entry.get("home_team")
        away = entry.get("away_team")
        if home and away:
            by_teams[frozenset({str(home), str(away)})] = entry
    return odds_by_match, by_teams


def _schedule_outcome_for_match(
    match_id: str,
    teams: tuple[str, str] | None,
    schedule_outcomes: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Resolve a schedule outcome by match id, then by unordered team pair.

    Team-pair fallback covers kickoff-date drift in MATCH ids (schedule date
    vs an older build artifact) so curated winners still lock on the canvas.
    """
    if not schedule_outcomes:
        return None
    schedule = schedule_outcomes.get(match_id)
    if schedule is not None:
        return schedule
    if teams is None:
        return None
    wanted = frozenset(teams)
    for entry in schedule_outcomes.values():
        home = entry.get("home_team")
        away = entry.get("away_team")
        if home and away and frozenset({str(home), str(away)}) == wanted:
            return entry
    return None


def _enrich_match_with_odds(
    element: dict[str, Any],
    odds_by_match: dict[str, dict[str, Any]],
    odds_by_teams: dict[frozenset[str], dict[str, Any]],
    schedule_outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from oddsgraph.flags import flag_url_or_blank

    data = dict(element.get("data") or {})
    match_id = str(data.get("id") or "")
    odds = odds_by_match.get(match_id)
    teams = split_match_teams(str(data.get("label") or ""))
    if odds is None and teams is not None:
        odds = odds_by_teams.get(frozenset(teams))
    if teams is not None:
        data["schedule_home"], data["schedule_away"] = teams
        data["schedule_label"] = str(data.get("label") or "")

    if odds is None:
        if teams is not None:
            data["home_team"], data["away_team"] = teams
    else:
        data["home_team"] = odds.get("home_team") or (teams[0] if teams else None)
        data["away_team"] = odds.get("away_team") or (teams[1] if teams else None)
        data["schedule_home"] = data.get("schedule_home") or data["home_team"]
        data["schedule_away"] = data.get("schedule_away") or data["away_team"]
        data["schedule_label"] = str(data.get("label") or "")
        data["match_start_epoch"] = odds.get("match_start_epoch")
        data["match_end_epoch"] = odds.get("match_end_epoch")
        data["winner_team"] = odds.get("winner_team")
        data["odds_series"] = list(odds.get("odds_series") or [])
        initial_hour = data.get("match_start_epoch")
        if data["odds_series"] and initial_hour is None:
            initial_hour = data["odds_series"][0]["h"]
        prob = home_prob_at_hour(data, initial_hour)
        if prob is not None:
            data["current_home_prob"] = prob

    schedule_teams = teams
    if schedule_teams is None and data.get("home_team") and data.get("away_team"):
        schedule_teams = (str(data["home_team"]), str(data["away_team"]))
    schedule = _schedule_outcome_for_match(match_id, schedule_teams, schedule_outcomes)
    if schedule:
        data.setdefault("match_start_epoch", schedule.get("match_start_epoch"))
        # Curated schedule outcomes win over soft odds-history locks so Final /
        # Third Place (and derived knockout results) stay authoritative.
        if schedule.get("winner_team"):
            data["winner_team"] = schedule["winner_team"]
            if schedule.get("match_end_epoch") is not None:
                data["match_end_epoch"] = schedule["match_end_epoch"]
        elif (
            data.get("match_end_epoch") is None
            and schedule.get("match_end_epoch") is not None
        ):
            data["match_end_epoch"] = schedule["match_end_epoch"]
        if not data.get("home_team") and schedule.get("home_team"):
            data["home_team"] = schedule["home_team"]
        if not data.get("away_team") and schedule.get("away_team"):
            data["away_team"] = schedule["away_team"]

    if data.get("home_team") or data.get("away_team"):
        data["home_flag"] = flag_url_or_blank(data.get("home_team"))
        data["away_flag"] = flag_url_or_blank(data.get("away_team"))
        data["flag_images"] = f"{data['home_flag']} {data['away_flag']}"
    return {**element, "data": data}


class ExplorerDataStore:
    """Process-session DuckDB connection with materialized nodes/edges tables.

    Caches bracket ``GraphSlice`` values keyed by parquet mtimes.
    Thread-safe enough for a single-user Dash explorer.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._nodes_mtime: float = -1.0
        self._edges_mtime: float = -1.0
        self._odds_mtime: float = -1.0
        self._stage_odds_mtime: float = -1.0
        self._bracket_cache: GraphSlice | None = None
        self._odds_by_match: dict[str, dict[str, Any]] = {}
        self._odds_by_teams: dict[frozenset[str], dict[str, Any]] = {}
        self._stage_odds_by_team: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._open()

    @property
    def nodes_path(self) -> Path:
        return self.settings.nodes_path

    @property
    def edges_path(self) -> Path:
        return self.settings.edges_path

    @property
    def odds_history_path(self) -> Path:
        return self.settings.odds_history_path

    @property
    def stage_odds_history_path(self) -> Path:
        return self.settings.stage_odds_history_path

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._bracket_cache = None
            self._odds_by_match = {}
            self._odds_by_teams = {}
            self._stage_odds_by_team = {}

    def _open(self) -> None:
        nodes_mtime, edges_mtime, odds_mtime, stage_mtime = _parquet_mtimes(
            self.nodes_path,
            self.edges_path,
            self.odds_history_path,
            self.stage_odds_history_path,
        )
        conn = duckdb.connect(database=":memory:")
        nodes_sql = quote_path(self.nodes_path)
        edges_sql = quote_path(self.edges_path)
        if self.nodes_path.exists():
            conn.execute(
                f"CREATE TABLE nodes AS SELECT * FROM read_parquet('{nodes_sql}')"
            )
        else:
            # Match export schema so queries on type/label/aliases do not BinderError.
            conn.register("_nodes_stub", table_with_schema([], NODE_SCHEMA))
            conn.execute("CREATE TABLE nodes AS SELECT * FROM _nodes_stub")
        if self.edges_path.exists():
            conn.execute(
                f"CREATE TABLE edges AS SELECT * FROM read_parquet('{edges_sql}')"
            )
        else:
            conn.register("_edges_stub", table_with_schema([], EDGE_SCHEMA))
            conn.execute("CREATE TABLE edges AS SELECT * FROM _edges_stub")
        self._conn = conn
        self._nodes_mtime = nodes_mtime
        self._edges_mtime = edges_mtime
        self._odds_mtime = odds_mtime
        self._stage_odds_mtime = stage_mtime
        self._bracket_cache = None
        self._odds_by_match = _load_odds_history_by_match(self.odds_history_path)
        self._odds_by_match, self._odds_by_teams = _odds_indexes(self._odds_by_match)
        self._stage_odds_by_team = _load_stage_odds_by_team(self.stage_odds_history_path)

    def refresh_if_stale(self) -> None:
        """Close and recreate the connection when parquet mtimes change."""
        with self._lock:
            nodes_mtime, edges_mtime, odds_mtime, stage_mtime = _parquet_mtimes(
                self.nodes_path,
                self.edges_path,
                self.odds_history_path,
                self.stage_odds_history_path,
            )
            if (
                nodes_mtime == self._nodes_mtime
                and edges_mtime == self._edges_mtime
                and odds_mtime == self._odds_mtime
                and stage_mtime == self._stage_odds_mtime
                and self._conn is not None
            ):
                return
            self.close()
            self._open()

    def _connection(self) -> duckdb.DuckDBPyConnection:
        self.refresh_if_stale()
        assert self._conn is not None
        return self._conn

    def bracket_elements(self) -> GraphSlice:
        with self._lock:
            self.refresh_if_stale()
            if self._bracket_cache is not None:
                return self._bracket_cache
            conn = self._connection()
            stage_labels = sorted(KNOCKOUT_STAGE_LABELS)
            placeholders = ", ".join(["?"] * len(stage_labels))
            nodes = _fetch_dicts(
                conn,
                f"""
                SELECT DISTINCT
                  m.*,
                  s.label AS stage
                FROM nodes m
                INNER JOIN edges e
                  ON e.source_id = m.canonical_id
                 AND e.edge_type = 'PART_OF'
                INNER JOIN nodes s
                  ON e.target_id = s.canonical_id
                 AND s.type = 'STAGE'
                WHERE m.type = 'MATCH'
                  AND s.label IN ({placeholders})
                ORDER BY m.label
                """,
                stage_labels,
            )
            if not nodes:
                slice_ = GraphSlice()
                self._bracket_cache = slice_
                return slice_

            match_ids = [n["canonical_id"] for n in nodes]
            id_placeholders = ", ".join(["?"] * len(match_ids))
            edges = _fetch_dicts(
                conn,
                f"""
                SELECT *
                FROM edges
                WHERE edge_type = 'ADVANCES_TO'
                  AND source_id IN ({id_placeholders})
                  AND target_id IN ({id_placeholders})
                ORDER BY source_id, target_id
                """,
                [*match_ids, *match_ids],
            )

            from oddsgraph.bracket import schedule_knockout_outcomes

            outcomes = schedule_knockout_outcomes()
            node_els = [
                _enrich_match_with_odds(
                    node_element(n, bracket=True),
                    self._odds_by_match,
                    self._odds_by_teams,
                    schedule_outcomes=outcomes,
                )
                for n in nodes
            ]
            edge_els = [edge_element(e) for e in edges]
            slice_ = GraphSlice(nodes=node_els, edges=edge_els)
            self._bracket_cache = slice_
            return slice_

    def odds_time_bounds(self) -> tuple[int | None, int | None]:
        """Return hour-aligned tournament start/end for the time slider.

        Bounds come from the official schedule through Final full-time so the
        Champion lock remains reachable. Falls back to kickoff bounds, then
        odds-history extrema when the schedule is empty.
        """
        with self._lock:
            self.refresh_if_stale()
            start, end = tournament_playback_bounds()
            if start is not None and end is not None:
                return start, end
            start, end = tournament_time_bounds()
            if start is not None and end is not None:
                return start, end
            hours: list[int] = []
            for entry in self._odds_by_match.values():
                for point in entry.get("odds_series") or []:
                    hours.append(int(point["h"]))
                for key in ("match_start_epoch", "match_end_epoch"):
                    value = entry.get(key)
                    if value is not None:
                        hours.append(int(value))
            for stages in self._stage_odds_by_team.values():
                for series in stages.values():
                    for point in series:
                        hours.append(int(point["h"]))
            if not hours:
                return None, None
            return min(hours), max(hours)

    def stage_odds_by_team(self) -> dict[str, dict[str, list[dict[str, Any]]]]:
        with self._lock:
            self.refresh_if_stale()
            return self._stage_odds_by_team

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        if not node_id:
            return None
        with self._lock:
            conn = self._connection()
            rows = _fetch_dicts(
                conn,
                """
                SELECT *
                FROM nodes
                WHERE canonical_id = ?
                LIMIT 1
                """,
                [node_id],
            )
        return rows[0] if rows else None

    def get_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
    ) -> dict[str, Any] | None:
        if not source_id or not target_id or not edge_type:
            return None
        with self._lock:
            conn = self._connection()
            rows = _fetch_dicts(
                conn,
                """
                SELECT *
                FROM edges
                WHERE source_id = ?
                  AND target_id = ?
                  AND edge_type = ?
                LIMIT 1
                """,
                [source_id, target_id, edge_type],
            )
        return rows[0] if rows else None

    def graph_counts(self) -> dict[str, Any]:
        report_path = self.settings.inference_report_path
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                node_counts = report.get("node_counts") or {}
                edge_counts = report.get("edge_counts") or {}
                # Empty histograms are not authoritative — fall through to parquet.
                if (
                    isinstance(node_counts, dict)
                    and isinstance(edge_counts, dict)
                    and (node_counts or edge_counts)
                ):
                    return {
                        "node_counts": {str(k): int(v) for k, v in node_counts.items()},
                        "edge_counts": {str(k): int(v) for k, v in edge_counts.items()},
                        "total_nodes": sum(int(v) for v in node_counts.values()),
                        "total_edges": sum(int(v) for v in edge_counts.values()),
                        "source": "inference_report",
                    }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

        with self._lock:
            conn = self._connection()
            node_rows = _fetch_dicts(
                conn,
                """
                SELECT type AS key, COUNT(*)::INTEGER AS count
                FROM nodes
                GROUP BY type
                ORDER BY type
                """,
            )
            edge_rows = _fetch_dicts(
                conn,
                """
                SELECT edge_type AS key, COUNT(*)::INTEGER AS count
                FROM edges
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


_STORE_LOCK = threading.Lock()
_STORES: dict[tuple[str, str], ExplorerDataStore] = {}


def get_store(settings: Settings) -> ExplorerDataStore:
    """Return a process-level store keyed by nodes/edges parquet paths."""
    key = (
        str(settings.nodes_path.resolve()),
        str(settings.edges_path.resolve()),
    )
    with _STORE_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = ExplorerDataStore(settings)
            _STORES[key] = store
        else:
            # Keep settings reference current (build_dir may share paths).
            store.settings = settings
            store.refresh_if_stale()
        return store


def clear_stores() -> None:
    """Close and drop all cached stores (for tests)."""
    with _STORE_LOCK:
        for store in _STORES.values():
            store.close()
        _STORES.clear()


def bracket_elements(settings: Settings) -> GraphSlice:
    """Return the knockout MATCH DAG for the mirrored tree UI.

    Each MATCH node is enriched with ``stage``, ``stage_rank``, ``short_label``,
    and optional hourly ``odds_series`` / ``current_home_prob`` when
    ``odds_history.parquet`` exists.
    """
    return get_store(settings).bracket_elements()


def odds_time_bounds(settings: Settings) -> tuple[int | None, int | None]:
    """Return hour-aligned tournament start through Final full-time for the slider."""
    return get_store(settings).odds_time_bounds()


def stage_odds_by_team(
    settings: Settings,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return cached stage-reach odds indexed by team then stage label."""
    return get_store(settings).stage_odds_by_team()


def get_node(settings: Settings, node_id: str) -> dict[str, Any] | None:
    """Return a single node row, or None if missing."""
    return get_store(settings).get_node(node_id)


def get_edge(
    settings: Settings,
    source_id: str,
    target_id: str,
    edge_type: str,
) -> dict[str, Any] | None:
    """Return a single edge row, or None if missing."""
    return get_store(settings).get_edge(source_id, target_id, edge_type)


def graph_counts(settings: Settings) -> dict[str, Any]:
    """Return node/edge totals and per-type counts for the header."""
    return get_store(settings).graph_counts()
