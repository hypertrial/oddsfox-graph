"""Build hourly stage-reach and tournament-winner probability history."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from oddsgraph import ids
from oddsgraph.config import Settings
from oddsgraph.export import write_parquet
from oddsgraph.odds_history import _to_epoch
from oddsgraph.propositions import _REACHES_STAGE_TITLES, _WORLD_CUP_WINNER_TITLE
from oddsgraph.reduce import quote_path

logger = logging.getLogger(__name__)

# Champion is modeled as wins_competition (World Cup Winner markets).
STAGE_ODDS_EVENT_TITLES: dict[str, str] = {
    **_REACHES_STAGE_TITLES,
    _WORLD_CUP_WINNER_TITLE.casefold(): "Champion",
}

STAGE_ODDS_HISTORY_SCHEMA = pa.schema(
    [
        ("team", pa.string()),
        ("stage_label", pa.string()),
        ("odds_hour_epoch", pa.int64()),
        ("reach_prob", pa.float64()),
        ("market_id", pa.string()),
    ]
)


def _parse_stage_event(event_title: str | None) -> str | None:
    if not event_title:
        return None
    return STAGE_ODDS_EVENT_TITLES.get(event_title.strip().casefold())


def _team_from_row(row: dict[str, Any]) -> str | None:
    raw = (row.get("group_item_title") or "").strip()
    if not raw:
        primary = (row.get("primary_outcome_label") or "").strip()
        if primary and primary.casefold() not in {"yes", "no"}:
            raw = primary
    if not raw:
        return None
    return ids.canonical_team_name(raw)


def _reach_prob_for_row(row: dict[str, Any]) -> float | None:
    """Interpret close_odds as Yes/team probability for stage markets."""
    close = row.get("close_odds")
    if close is None:
        return None
    prob = float(close)
    primary = (row.get("primary_outcome_label") or "").strip()
    if not primary:
        return prob
    folded = primary.casefold()
    if folded == "yes":
        return prob
    if folded == "no":
        return 1.0 - prob
    # Team-named outcomes (World Cup Winner style): odds already for that team.
    return prob


def _query_stage_rows(input_glob: str) -> list[dict[str, Any]]:
    titles = sorted(STAGE_ODDS_EVENT_TITLES)
    title_list = ", ".join(f"'{t.replace(chr(39), chr(39) + chr(39))}'" for t in titles)
    query = f"""
        SELECT
            market_id,
            event_title,
            group_item_title,
            primary_outcome_label,
            close_odds,
            odds_hour_epoch
        FROM read_parquet('{quote_path(input_glob)}')
        WHERE odds_hour_epoch IS NOT NULL
          AND close_odds IS NOT NULL
          AND lower(trim(event_title)) IN ({title_list})
        ORDER BY market_id, odds_hour_epoch
    """
    con = duckdb.connect()
    try:
        table = con.execute(query).arrow()
    finally:
        con.close()
    if not isinstance(table, pa.Table):
        table = table.read_all()
    return table.to_pylist()


def build_stage_odds_history_rows(
    stage_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize hourly stage/tournament markets into reachable-prob rows."""
    out: list[dict[str, Any]] = []
    for row in stage_rows:
        stage_label = _parse_stage_event(row.get("event_title"))
        team = _team_from_row(row)
        hour = _to_epoch(row.get("odds_hour_epoch"))
        reach = _reach_prob_for_row(row)
        if stage_label is None or team is None or hour is None or reach is None:
            continue
        out.append(
            {
                "team": team,
                "stage_label": stage_label,
                "odds_hour_epoch": hour,
                "reach_prob": reach,
                "market_id": str(row.get("market_id") or ""),
            }
        )
    # Multi-file globs can emit duplicate (team, stage, hour) points.
    deduped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in out:
        key = (row["team"], row["stage_label"], int(row["odds_hour_epoch"]))
        deduped[key] = row
    out = sorted(
        deduped.values(),
        key=lambda r: (r["team"], r["stage_label"], r["odds_hour_epoch"]),
    )
    return out


def build_stage_odds_history(settings: Settings) -> Path:
    """Write ``stage_odds_history.parquet`` for stage-reach / champion probs."""
    settings.ensure_dirs()
    raw_rows = _query_stage_rows(settings.resolve_input_glob())
    rows = build_stage_odds_history_rows(raw_rows)
    output_path = settings.stage_odds_history_path
    write_parquet(output_path, rows, STAGE_ODDS_HISTORY_SCHEMA)
    teams = {r["team"] for r in rows}
    stages = {r["stage_label"] for r in rows}
    logger.info(
        "Wrote %d stage-odds rows (%d teams, %d stages) to %s",
        len(rows),
        len(teams),
        len(stages),
        output_path,
    )
    return output_path
