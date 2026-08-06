from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings


def test_resolve_input_glob_uses_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "custom-data"
    data_dir.mkdir()
    parquet_path = data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet"
    table = pa.table({"market_id": ["1"], "event_id": ["100"]})
    pq.write_table(table, parquet_path)

    settings = Settings()
    settings.data_dir = data_dir
    resolved = settings.resolve_input_glob()

    assert resolved.startswith(str(data_dir))
    assert "market_hourly_odds" in resolved


def test_resolve_input_glob_explicit_data_dir_does_not_fall_back(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty-data"
    empty.mkdir()
    settings = Settings()
    settings.data_dir = empty
    resolved = settings.resolve_input_glob()
    assert resolved == str(empty / "*market_hourly_odds*.parquet")
