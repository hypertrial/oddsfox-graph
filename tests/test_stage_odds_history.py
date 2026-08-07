"""Tests for stage-odds history construction."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.stage_odds_history import (
    STAGE_ODDS_HISTORY_SCHEMA,
    build_stage_odds_history,
    build_stage_odds_history_rows,
)


def _stage_row(
    *,
    market_id: str = "m-stage-1",
    event_title: str = "World Cup: Nation to Reach Round of 16",
    group_item_title: str = "Brazil",
    primary_outcome_label: str = "Yes",
    close_odds: float = 0.4,
    odds_hour_epoch: int = 1_780_000_000,
) -> dict:
    return {
        "market_id": market_id,
        "event_title": event_title,
        "group_item_title": group_item_title,
        "primary_outcome_label": primary_outcome_label,
        "close_odds": close_odds,
        "odds_hour_epoch": odds_hour_epoch,
    }


def test_build_stage_odds_history_rows_normalizes_yes_no_and_winner() -> None:
    rows = build_stage_odds_history_rows(
        [
            _stage_row(close_odds=0.55),
            _stage_row(
                market_id="m-no",
                primary_outcome_label="No",
                close_odds=0.2,
                odds_hour_epoch=1_780_003_600,
            ),
            _stage_row(
                market_id="m-champ",
                event_title="World Cup Winner",
                group_item_title="Argentina",
                primary_outcome_label="Argentina",
                close_odds=0.18,
            ),
            _stage_row(
                market_id="m-alias",
                event_title="World Cup: Nation to Reach Final",
                group_item_title="Côte d'Ivoire",
                close_odds=0.07,
            ),
        ]
    )
    brazil_r16 = [
        r for r in rows if r["team"] == "Brazil" and r["stage_label"] == "Round of 16"
    ]
    assert len(brazil_r16) == 2
    assert brazil_r16[0]["reach_prob"] == 0.55
    assert abs(brazil_r16[1]["reach_prob"] - 0.8) < 1e-9
    champ = next(r for r in rows if r["stage_label"] == "Champion")
    assert champ["team"] == "Argentina"
    assert champ["reach_prob"] == 0.18
    final = next(r for r in rows if r["stage_label"] == "Final")
    assert final["team"] == "Ivory Coast"
    assert final["reach_prob"] == 0.07


def test_build_stage_odds_history_writes_parquet(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                _stage_row(),
                _stage_row(
                    market_id="m2",
                    event_title="World Cup: Nation to Reach Quarterfinals",
                    group_item_title="France",
                    close_odds=0.33,
                ),
                {
                    **_stage_row(market_id="ignored"),
                    "event_title": "Unrelated Event",
                },
            ]
        ),
        data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet",
    )
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.configure_data_dir(data_dir)
    settings.ensure_dirs()

    out = build_stage_odds_history(settings)
    assert out.exists()
    table = pq.read_table(out)
    assert table.schema.equals(STAGE_ODDS_HISTORY_SCHEMA)
    assert table.num_rows == 2
    teams = set(table.column("team").to_pylist())
    assert teams == {"Brazil", "France"}
