from pathlib import Path

import pyarrow.parquet as pq

from oddsgraph.config import Settings
from oddsgraph.reduce import reduce_semantic_markets


def test_reduce_writes_semantic_markets(tmp_path: Path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.semantic_markets_path = tmp_path / "build" / "semantic_markets.parquet"
    output = reduce_semantic_markets(settings)
    assert output.exists()
    table = pq.read_table(output)
    assert table.num_rows > 0
    assert "market_id" in table.column_names
    assert "event_id" in table.column_names
