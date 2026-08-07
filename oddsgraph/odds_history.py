"""Build hourly knockout win-probability history from Polymarket advance markets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa

from oddsgraph import ids
from oddsgraph.bracket import KNOCKOUT_STAGE_RANK, _kickoff_date, load_wc2026_schedule
from oddsgraph.config import Settings
from oddsgraph.export import write_parquet
from oddsgraph.fragments import match_local_id

logger = logging.getLogger(__name__)

ODDS_HISTORY_SCHEMA = pa.schema(
    [
        ("match_canonical_id", pa.string()),
        ("home_team", pa.string()),
        ("away_team", pa.string()),
        ("odds_hour_epoch", pa.int64()),
        ("home_prob", pa.float64()),
        ("away_prob", pa.float64()),
        ("match_start_epoch", pa.int64()),
        ("match_end_epoch", pa.int64()),
        ("winner_team", pa.string()),
    ]
)

_VS_SPLIT = " vs. "


@dataclass(frozen=True)
class KnockoutFixture:
    """Schedule fixture used to join advance-market odds."""

    fifa_match_id: int
    stage_key: str
    home_team: str
    away_team: str
    kickoff_at_utc: str
    match_canonical_id: str

    @property
    def team_key(self) -> frozenset[str]:
        return frozenset({self.home_team, self.away_team})


def _to_epoch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def _parse_event_teams(event_title: str | None) -> frozenset[str] | None:
    """Extract canonical team pair from a Polymarket advance-market event title."""
    if not event_title:
        return None
    title = event_title.split(" - ", 1)[0].strip()
    parts = title.replace(" vs ", _VS_SPLIT).split(_VS_SPLIT)
    if len(parts) != 2:
        return None
    return frozenset(
        {
            ids.canonical_team_name(parts[0].strip()),
            ids.canonical_team_name(parts[1].strip()),
        }
    )


def load_knockout_fixtures() -> list[KnockoutFixture]:
    """Load knockout fixtures with MATCH ids matching the official bracket."""
    schedule = load_wc2026_schedule()
    fixtures: list[KnockoutFixture] = []
    for raw in schedule.get("fixtures") or []:
        stage_key = raw["stage_key"]
        if stage_key not in KNOCKOUT_STAGE_RANK:
            continue
        home = ids.canonical_team_name(raw["home_team"])
        away = ids.canonical_team_name(raw["away_team"])
        kickoff = raw.get("kickoff_at_utc") or ""
        fixtures.append(
            KnockoutFixture(
                fifa_match_id=int(raw["fifa_match_id"]),
                stage_key=stage_key,
                home_team=home,
                away_team=away,
                kickoff_at_utc=kickoff,
                match_canonical_id=match_local_id(home, away, _kickoff_date(kickoff)),
            )
        )
    return fixtures


def _probs_for_hour(
    *,
    home_team: str,
    away_team: str,
    primary_outcome_label: str | None,
    close_odds: float | None,
) -> tuple[float, float] | None:
    if close_odds is None:
        return None
    primary = ids.canonical_team_name(primary_outcome_label or "")
    prob = float(close_odds)
    if primary == home_team:
        return prob, 1.0 - prob
    if primary == away_team:
        return 1.0 - prob, prob
    return None


def _resolve_winner(
    *,
    home_team: str,
    away_team: str,
    winning_outcome: str | None,
    last_home_prob: float | None,
    last_away_prob: float | None,
) -> str | None:
    if winning_outcome:
        winner = ids.canonical_team_name(winning_outcome)
        if winner in {home_team, away_team}:
            return winner
    if last_home_prob is None or last_away_prob is None:
        return None
    if last_home_prob > last_away_prob:
        return home_team
    if last_away_prob > last_home_prob:
        return away_team
    return None


def _query_advance_rows(input_glob: str) -> list[dict[str, Any]]:
    from oddsgraph.hourly_scan import split_history_source_rows

    advance_rows, _stage_rows = split_history_source_rows(input_glob)
    return advance_rows


def build_odds_history_rows(
    fixtures: list[KnockoutFixture],
    advance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join knockout fixtures to hourly advance-market rows."""
    fixture_by_teams = {fixture.team_key: fixture for fixture in fixtures}
    grouped: dict[str, list[dict[str, Any]]] = {}
    market_meta: dict[str, dict[str, Any]] = {}

    for row in advance_rows:
        teams = _parse_event_teams(row.get("event_title"))
        if teams is None or teams not in fixture_by_teams:
            continue
        market_id = str(row["market_id"])
        grouped.setdefault(market_id, []).append(row)
        meta = market_meta.setdefault(market_id, {"teams": teams})
        if row.get("game_start_time") is not None:
            meta["game_start_time"] = row["game_start_time"]
        if row.get("event_finished_at") is not None:
            meta["event_finished_at"] = row["event_finished_at"]
        if row.get("winning_outcome"):
            meta["winning_outcome"] = row["winning_outcome"]
        if row.get("is_resolved"):
            meta["is_resolved"] = True

    out: list[dict[str, Any]] = []
    matched_fixtures: set[str] = set()

    for market_id, rows in grouped.items():
        meta = market_meta[market_id]
        fixture = fixture_by_teams[meta["teams"]]
        matched_fixtures.add(fixture.match_canonical_id)
        series: list[tuple[int, float, float]] = []
        for row in rows:
            probs = _probs_for_hour(
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                primary_outcome_label=row.get("primary_outcome_label"),
                close_odds=row.get("close_odds"),
            )
            hour = _to_epoch(row.get("odds_hour_epoch"))
            if probs is None or hour is None:
                continue
            series.append((hour, probs[0], probs[1]))

        if not series:
            continue
        # Multi-file input globs can repeat the same market hour; keep one point.
        by_hour: dict[int, tuple[int, float, float]] = {}
        for hour, home_prob, away_prob in series:
            by_hour[hour] = (hour, home_prob, away_prob)
        series = sorted(by_hour.values(), key=lambda item: item[0])

        kickoff_epoch = _to_epoch(fixture.kickoff_at_utc)
        start_epoch = (
            _to_epoch(meta.get("game_start_time"))
            or kickoff_epoch
            or series[0][0]
        )
        finished_epoch = _to_epoch(meta.get("event_finished_at"))
        # Only lock when the market is finished or resolved — never treat the
        # last observed hour of a live series as match end.
        has_result = bool(
            finished_epoch is not None
            or meta.get("winning_outcome")
            or meta.get("is_resolved")
        )
        if has_result:
            end_epoch = finished_epoch or series[-1][0]
            winner = _resolve_winner(
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                winning_outcome=meta.get("winning_outcome"),
                last_home_prob=series[-1][1],
                last_away_prob=series[-1][2],
            )
        else:
            end_epoch = None
            winner = None

        for hour, home_prob, away_prob in series:
            out.append(
                {
                    "match_canonical_id": fixture.match_canonical_id,
                    "home_team": fixture.home_team,
                    "away_team": fixture.away_team,
                    "odds_hour_epoch": hour,
                    "home_prob": home_prob,
                    "away_prob": away_prob,
                    "match_start_epoch": start_epoch,
                    "match_end_epoch": end_epoch,
                    "winner_team": winner,
                }
            )

    missing = [
        fixture.match_canonical_id
        for fixture in fixtures
        if fixture.match_canonical_id not in matched_fixtures
    ]
    if missing:
        logger.warning(
            "No soccer_team_to_advance series for %d knockout matches: %s",
            len(missing),
            ", ".join(missing[:5]) + ("..." if len(missing) > 5 else ""),
        )
    return out


def build_odds_history(settings: Settings) -> Path:
    """Write ``odds_history.parquet`` for knockout MATCH win probabilities."""
    match_path, _stage_path = build_odds_histories(settings)
    return match_path


def build_odds_histories(settings: Settings) -> tuple[Path, Path]:
    """Write match + stage odds histories from a single source parquet scan."""
    from oddsgraph.hourly_scan import split_history_source_rows
    from oddsgraph.stage_odds_history import (
        STAGE_ODDS_HISTORY_SCHEMA,
        build_stage_odds_history_rows,
    )

    settings.ensure_dirs()
    fixtures = load_knockout_fixtures()
    advance_rows, stage_raw = split_history_source_rows(settings.resolve_input_glob())
    match_rows = build_odds_history_rows(fixtures, advance_rows)
    stage_rows = build_stage_odds_history_rows(stage_raw)

    match_path = settings.odds_history_path
    stage_path = settings.stage_odds_history_path
    write_parquet(match_path, match_rows, ODDS_HISTORY_SCHEMA)
    write_parquet(stage_path, stage_rows, STAGE_ODDS_HISTORY_SCHEMA)

    logger.info(
        "Wrote %d odds-history rows for %d knockout fixtures to %s",
        len(match_rows),
        len(fixtures),
        match_path,
    )
    teams = {r["team"] for r in stage_rows}
    stages = {r["stage_label"] for r in stage_rows}
    logger.info(
        "Wrote %d stage-odds rows (%d teams, %d stages) to %s",
        len(stage_rows),
        len(teams),
        len(stages),
        stage_path,
    )
    return match_path, stage_path
