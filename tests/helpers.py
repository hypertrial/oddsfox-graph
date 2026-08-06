"""Shared test helpers."""

from __future__ import annotations

import json
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.schema import GraphFragment, SemanticMarket

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
GOLDEN_MARKETS_PATH = FIXTURES_DIR / "golden_semantic_markets.parquet"
FRAGMENTS_DIR = FIXTURES_DIR / "fragments"


def make_settings(tmp_path: Path) -> Settings:
    """Create Settings rooted at tmp_path with aligned build/data dirs."""
    settings = Settings()
    settings.configure_repo_root(tmp_path)
    settings.configure_build_dir(tmp_path / "build")
    settings.configure_data_dir(tmp_path / "data")
    settings.ensure_dirs()
    return settings


def load_golden_markets() -> list[SemanticMarket]:
    from oddsgraph.reduce import load_semantic_markets

    return load_semantic_markets(GOLDEN_MARKETS_PATH)


def load_fixture_fragment(event_id: str) -> GraphFragment:
    path = FRAGMENTS_DIR / f"{event_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return GraphFragment.model_validate(data)
