from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oddsgraph.config import Settings


def test_resolve_input_glob_explicit_default_data_dir_is_exclusive(
    tmp_path: Path,
) -> None:
    """Passing --data-dir equal to <repo>/data must still skip repo-root fallback."""
    (tmp_path / "data").mkdir()
    root_parquet = tmp_path / "polymarket_wc2026_market_hourly_odds_root.parquet"
    pq.write_table(pa.table({"market_id": ["1"]}), root_parquet)

    settings = Settings()
    settings.configure_repo_root(tmp_path)
    settings.configure_data_dir(tmp_path / "data")
    resolved = settings.resolve_input_glob()
    assert resolved.startswith(str(tmp_path / "data"))
    assert "root.parquet" not in resolved


def test_resolve_input_glob_uses_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "custom-data"
    data_dir.mkdir()
    parquet_path = data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet"
    table = pa.table({"market_id": ["1"], "event_id": ["100"]})
    pq.write_table(table, parquet_path)

    settings = Settings()
    settings.configure_data_dir(data_dir)
    resolved = settings.resolve_input_glob()

    assert resolved.startswith(str(data_dir))
    assert "market_hourly_odds" in resolved


def test_resolve_input_glob_explicit_data_dir_does_not_fall_back(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty-data"
    empty.mkdir()
    settings = Settings()
    settings.configure_data_dir(empty)
    resolved = settings.resolve_input_glob()
    assert resolved == str(empty / "*market_hourly_odds*.parquet")


def test_configure_repo_root_aligns_default_data_dir(tmp_path: Path) -> None:
    settings = Settings()
    settings.configure_repo_root(tmp_path)
    assert settings.data_dir == tmp_path / "data"
    assert settings.models_dir == tmp_path / "models"
