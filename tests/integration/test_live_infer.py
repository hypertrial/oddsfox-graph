import os
from pathlib import Path

import pytest

from oddsfox_graph.config import Settings
from oddsfox_graph.infer import infer_event_fragments
from oddsfox_graph.llm import LocalGraphLLM
from oddsfox_graph.reduce import load_semantic_markets

from tests.helpers import GOLDEN_MARKETS_PATH


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("OF_LIVE_MODEL_TEST") != "1",
    reason="Set OF_LIVE_MODEL_TEST=1 to run live model tests",
)
@pytest.mark.skipif(
    not Path("models/qwen3-4b-q4_k_m.gguf").exists(),
    reason="Model file not found",
)
def test_live_infer_single_event(tmp_path: Path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.fragments_dir = tmp_path / "build" / "fragments"
    settings.failed_fragments_dir = tmp_path / "build" / "fragments" / "_failed"
    settings.inference_report_path = tmp_path / "build" / "inference_report.json"
    settings.resume = False
    settings.limit_events = 1
    settings.event_ids = ["351746"]

    markets = load_semantic_markets(GOLDEN_MARKETS_PATH)
    llm = LocalGraphLLM(settings)
    results = infer_event_fragments(settings, markets, llm=llm)
    assert "351746" in results
    assert len(results["351746"].nodes) > 0
