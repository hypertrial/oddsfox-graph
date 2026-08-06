from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.reduce import reduce_semantic_markets


def _write_raw_odds_fixture(path: Path) -> None:
    table = pa.table(
        {
            "market_id": ["m1"],
            "event_id": ["e1"],
            "event_slug": ["slug"],
            "event_title": ["Brazil vs. Morocco - Exact Score"],
            "event_description": [""],
            "question": ["Will Brazil win?"],
            "description": [""],
            "market_slug": ["slug-market"],
            "sports_market_type": ["soccer_match_winner"],
            "group_item_title": [""],
            "outcomes": ['["Brazil","Morocco"]'],
            "tags": ["[]"],
            "event_tags": ["[]"],
            "game_start_time": ["2026-06-01T00:00:00Z"],
            "end_time": ["2026-06-01T02:00:00Z"],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def test_reduce_writes_semantic_markets(tmp_path: Path) -> None:
    settings = Settings()
    settings.repo_root = tmp_path
    settings.build_dir = tmp_path / "build"
    settings.semantic_markets_path = settings.build_dir / "semantic_markets.parquet"
    _write_raw_odds_fixture(tmp_path / "data" / "market_hourly_odds_fixture.parquet")

    output = reduce_semantic_markets(settings)
    assert output.exists()
    table = pq.read_table(output)
    assert table.num_rows > 0
    assert "market_id" in table.column_names
    assert "event_id" in table.column_names
