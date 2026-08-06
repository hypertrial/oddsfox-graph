"""Shared test helpers."""

from __future__ import annotations

import json
from pathlib import Path

from oddsgraph.schema import GraphFragment, SemanticMarket

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
GOLDEN_MARKETS_PATH = FIXTURES_DIR / "golden_semantic_markets.parquet"
FRAGMENTS_DIR = FIXTURES_DIR / "fragments"


def load_golden_markets() -> list[SemanticMarket]:
    from oddsgraph.reduce import load_semantic_markets

    return load_semantic_markets(GOLDEN_MARKETS_PATH)


def load_fixture_fragment(event_id: str) -> GraphFragment:
    path = FRAGMENTS_DIR / f"{event_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return GraphFragment.model_validate(data)
