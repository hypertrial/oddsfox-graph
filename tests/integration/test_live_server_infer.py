import os
from pathlib import Path

import httpx
import pytest

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments
from oddsgraph.llm import build_graph_llm
from oddsgraph.reduce import load_semantic_markets

from tests.helpers import GOLDEN_MARKETS_PATH


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ODDSGRAPH_LIVE_SERVER_TEST") != "1",
    reason="Set ODDSGRAPH_LIVE_SERVER_TEST=1 to run live server integration tests",
)
def test_live_server_infer_single_event(tmp_path: Path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.fragments_dir = tmp_path / "build" / "fragments"
    settings.failed_fragments_dir = tmp_path / "build" / "fragments" / "_failed"
    settings.inference_report_path = tmp_path / "build" / "inference_report.json"
    settings.llm_backend = "server"
    settings.llm_concurrency = 2
    settings.resume = False
    settings.limit_events = 1
    settings.event_ids = ["351746"]

    try:
        httpx.get(f"{settings.server_base_url.rstrip('/')}/health", timeout=2.0)
    except httpx.HTTPError:
        pytest.skip("llama-server is not reachable")

    markets = load_semantic_markets(GOLDEN_MARKETS_PATH)
    llm = build_graph_llm(settings)
    results = infer_event_fragments(settings, markets, llm=llm)
    assert "351746" in results
