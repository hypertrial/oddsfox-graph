"""Live MLX infer integration test (Apple Silicon only)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments
from oddsgraph.reduce import load_semantic_markets

from tests.helpers import GOLDEN_MARKETS_PATH


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="MLX requires Apple Silicon")
@pytest.mark.skipif(
    os.environ.get("ODDSGRAPH_LIVE_MLX_TEST") != "1",
    reason="Set ODDSGRAPH_LIVE_MLX_TEST=1 to run live MLX tests",
)
@pytest.mark.skipif(
    not Path("models/qwen3-4b-mlx").exists(),
    reason="MLX model directory not found",
)
def test_live_mlx_infer_single_event(tmp_path: Path) -> None:
    from oddsgraph.llm_mlx import MLXGraphLLM

    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    settings.resume = False
    settings.limit_events = 1
    settings.event_ids = ["351746"]
    settings.llm_backend = "mlx"
    settings.deterministic_topology = False

    markets = load_semantic_markets(GOLDEN_MARKETS_PATH)
    llm = MLXGraphLLM(settings)
    results = infer_event_fragments(settings, markets, llm=llm)
    assert "351746" in results
    assert len(results["351746"].nodes) > 0
