"""Regression tests for DuckDB SQL escaping in reduce helpers."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from oddsgraph.config import Settings
from oddsgraph.reduce import (
    quote_path,
    quote_sql_literal,
    list_semantic_market_event_ids,
    load_semantic_markets,
    reduce_semantic_markets,
)


def _write_markets(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _market_row(
    *,
    market_id: str = "m1",
    event_id: str = "evt-1",
    question: str = "Will it rain?",
) -> dict:
    return {
        "market_id": market_id,
        "event_id": event_id,
        "event_slug": None,
        "event_title": "Weather",
        "event_description": None,
        "question": question,
        "description": None,
        "market_slug": None,
        "sports_market_type": None,
        "group_item_title": None,
        "outcomes": '["Yes", "No"]',
        "tags": None,
        "event_tags": None,
        "game_start_time": None,
        "end_time": None,
    }


def test_quote_sql_literal_escapes_apostrophes() -> None:
    assert quote_sql_literal("O'Brien") == "O''Brien"
    assert quote_path(Path("/tmp/O'Brien/data.parquet")) == "/tmp/O''Brien/data.parquet"


def test_list_and_load_handle_apostrophe_in_path(tmp_path: Path) -> None:
    markets_dir = tmp_path / "O'Brien" / "data"
    parquet_path = markets_dir / "semantic_markets.parquet"
    _write_markets(
        parquet_path,
        [
            _market_row(market_id="m1", event_id="e1"),
            _market_row(market_id="m2", event_id="e2"),
        ],
    )

    assert list_semantic_market_event_ids(parquet_path) == ["e1", "e2"]
    loaded = load_semantic_markets(parquet_path, event_ids=["e1"])
    assert [m.market_id for m in loaded] == ["m1"]


def test_load_semantic_markets_escapes_quoted_event_ids(tmp_path: Path) -> None:
    parquet_path = tmp_path / "markets.parquet"
    _write_markets(
        parquet_path,
        [
            _market_row(market_id="m1", event_id="O'Brien"),
            _market_row(market_id="m2", event_id="safe"),
        ],
    )

    loaded = load_semantic_markets(parquet_path, event_ids=["O'Brien"])
    assert len(loaded) == 1
    assert loaded[0].event_id == "O'Brien"
    assert loaded[0].market_id == "m1"


def test_reduce_semantic_markets_handles_apostrophe_in_data_dir(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "O'Brien" / "data"
    data_dir.mkdir(parents=True)
    _write_markets(
        data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet",
        [_market_row()],
    )

    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.configure_data_dir(data_dir)
    settings.ensure_dirs()

    out = reduce_semantic_markets(settings)
    assert out.exists()
    markets = load_semantic_markets(out)
    assert len(markets) == 1
    assert markets[0].market_id == "m1"


def test_reduce_batches_tolerate_null_then_list_optional_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oddsgraph.reduce as reduce_mod

    monkeypatch.setattr(reduce_mod, "REDUCE_BATCH_SIZE", 1)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = [
        _market_row(market_id="m1", event_id="e1"),
        {
            **_market_row(market_id="m2", event_id="e2"),
            "tags": '["soccer"]',
            "event_tags": '["wc2026"]',
        },
    ]
    _write_markets(
        data_dir / "polymarket_wc2026_market_hourly_odds_test.parquet",
        rows,
    )

    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.configure_data_dir(data_dir)
    settings.ensure_dirs()

    out = reduce_semantic_markets(settings)
    markets = load_semantic_markets(out)
    assert {m.market_id for m in markets} == {"m1", "m2"}
