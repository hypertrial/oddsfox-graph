"""Tests for knockout odds-history construction."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.odds_history import (
    ODDS_HISTORY_SCHEMA,
    KnockoutFixture,
    build_odds_history,
    build_odds_history_rows,
    load_knockout_fixtures,
)


def _write_hourly(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _advance_row(
    *,
    market_id: str = "m-adv-1",
    event_title: str = "Paraguay vs. France - More Markets",
    primary_outcome_label: str = "Paraguay",
    close_odds: float = 0.2,
    odds_hour_epoch: int = 1_783_200_000,
    game_start_time: str = "2026-07-04T21:00:00",
    event_finished_at: str | None = "2026-07-04T23:10:00",
    is_resolved: bool | None = None,
    winning_outcome: str | None = None,
) -> dict:
    return {
        "market_id": market_id,
        "event_title": event_title,
        "primary_outcome_label": primary_outcome_label,
        "close_odds": close_odds,
        "odds_hour_epoch": odds_hour_epoch,
        "game_start_time": game_start_time,
        "end_time": game_start_time,
        "event_finished_at": event_finished_at,
        "is_resolved": is_resolved,
        "winning_outcome": winning_outcome,
        "sports_market_type": "soccer_team_to_advance",
    }


def test_load_knockout_fixtures_has_32_matches() -> None:
    fixtures = load_knockout_fixtures()
    assert len(fixtures) == 32
    assert all(f.match_canonical_id.startswith("match:") for f in fixtures)
    finals = [f for f in fixtures if f.stage_key == "final"]
    assert len(finals) == 1
    assert finals[0].home_team == "Spain"
    assert finals[0].away_team == "Argentina"


def test_build_odds_history_rows_normalizes_ivory_coast_and_locks_winner() -> None:
    fixtures = [
        KnockoutFixture(
            fifa_match_id=78,
            stage_key="round_of_32",
            home_team="Ivory Coast",
            away_team="Norway",
            kickoff_at_utc="2026-06-30T23:00:00",
            match_canonical_id="match:ivory-coast-vs-norway-2026-06-30",
        )
    ]
    rows = build_odds_history_rows(
        fixtures,
        [
            _advance_row(
                market_id="m1",
                event_title="Côte d'Ivoire vs. Norway - More Markets",
                primary_outcome_label="Côte d'Ivoire",
                close_odds=0.35,
                odds_hour_epoch=1_782_800_000,
                game_start_time="2026-06-30T23:00:00",
                event_finished_at="2026-07-01T01:05:00",
                winning_outcome=None,
            ),
            _advance_row(
                market_id="m1",
                event_title="Côte d'Ivoire vs. Norway - More Markets",
                primary_outcome_label="Côte d'Ivoire",
                close_odds=0.12,
                odds_hour_epoch=1_782_807_200,
                game_start_time="2026-06-30T23:00:00",
                event_finished_at="2026-07-01T01:05:00",
                winning_outcome=None,
            ),
        ],
    )
    assert len(rows) == 2
    assert rows[0]["home_team"] == "Ivory Coast"
    assert rows[0]["home_prob"] == 0.35
    assert abs(rows[0]["away_prob"] - 0.65) < 1e-9
    # Last hour favors Norway -> winner lock fallback.
    assert rows[-1]["winner_team"] == "Norway"
    from datetime import datetime, timezone

    expected_end = int(
        datetime(2026, 7, 1, 1, 5, tzinfo=timezone.utc).timestamp()
    )
    assert rows[-1]["match_end_epoch"] == expected_end


def test_build_odds_history_rows_prefers_winning_outcome() -> None:
    fixtures = [
        KnockoutFixture(
            fifa_match_id=89,
            stage_key="round_of_16",
            home_team="Paraguay",
            away_team="France",
            kickoff_at_utc="2026-07-04T21:00:00",
            match_canonical_id="match:paraguay-vs-france-2026-07-04",
        )
    ]
    rows = build_odds_history_rows(
        fixtures,
        [
            _advance_row(
                close_odds=0.9,
                odds_hour_epoch=1_783_200_000,
                winning_outcome="France",
                is_resolved=True,
            ),
            _advance_row(
                close_odds=0.95,
                odds_hour_epoch=1_783_203_600,
                winning_outcome="France",
                is_resolved=True,
            ),
        ],
    )
    assert rows[0]["home_prob"] == 0.9
    assert rows[0]["winner_team"] == "France"


def test_build_odds_history_writes_parquet(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_hourly(
        data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet",
        [
            _advance_row(
                odds_hour_epoch=1_783_200_000,
                close_odds=0.25,
            ),
            _advance_row(
                odds_hour_epoch=1_783_203_600,
                close_odds=0.1,
                winning_outcome="France",
                is_resolved=True,
            ),
            # Ignored non-advance market.
            {
                **_advance_row(market_id="m-money", close_odds=0.5),
                "sports_market_type": "moneyline",
            },
        ],
    )
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.configure_data_dir(data_dir)
    settings.ensure_dirs()

    out = build_odds_history(settings)
    assert out.exists()
    table = pq.read_table(out)
    assert table.schema.equals(ODDS_HISTORY_SCHEMA)
    assert table.num_rows == 2
    ids = set(table.column("match_canonical_id").to_pylist())
    assert ids == {"match:paraguay-vs-france-2026-07-04"}
    assert table.column("winner_team").to_pylist() == ["France", "France"]
