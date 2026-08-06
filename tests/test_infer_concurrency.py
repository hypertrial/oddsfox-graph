"""Tests for concurrent infer dispatch and chunking defaults."""

from __future__ import annotations

import json
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments
from oddsgraph.llm import BaseGraphLLM
from oddsgraph.prompts import chunk_markets_for_prompt
from oddsgraph.schema import GraphFragment, SemanticMarket


class _RecordingGraphLLM(BaseGraphLLM):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[str, int]] = []

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self.calls.append((user_prompt, max_tokens))
        return json.dumps({"nodes": [], "edges": []})


def _market(market_id: str, event_id: str = "100") -> SemanticMarket:
    return SemanticMarket(
        market_id=market_id,
        event_id=event_id,
        question=f"Question {market_id}",
    )


def test_infer_concurrency_runs_multiple_chunks(tmp_path: Path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.fragments_dir = settings.build_dir / "fragments"
    settings.failed_fragments_dir = settings.fragments_dir / "_failed"
    settings.inference_report_path = settings.build_dir / "inference_report.json"
    settings.llm_backend = "server"
    settings.llm_concurrency = 3
    settings.resume = False
    settings.chunk_token_budget = 500
    settings.chunk_output_token_budget = 500
    settings.max_markets_per_chunk = 2
    settings.n_ctx = 2000
    settings.ensure_dirs()

    markets = [_market(str(i)) for i in range(6)]
    llm = _RecordingGraphLLM(settings)
    results = infer_event_fragments(settings, markets, llm=llm)
    assert len(results) == 1
    assert len(llm.calls) == 3
    assert (settings.fragments_dir / "100.json").exists()


def test_infer_inprocess_clamps_concurrency(tmp_path: Path, caplog) -> None:
    settings = Settings()
    settings.build_dir = tmp_path / "build"
    settings.fragments_dir = settings.build_dir / "fragments"
    settings.failed_fragments_dir = settings.fragments_dir / "_failed"
    settings.inference_report_path = settings.build_dir / "inference_report.json"
    settings.llm_backend = "inprocess"
    settings.llm_concurrency = 4
    settings.resume = False
    settings.ensure_dirs()

    markets = [_market("1"), _market("2")]
    llm = _RecordingGraphLLM(settings)
    infer_event_fragments(settings, markets, llm=llm)
    assert "ignored for inprocess backend" in caplog.text


def test_new_defaults_produce_fewer_chunks_than_legacy() -> None:
    markets = [_market(str(i)) for i in range(40)]
    legacy = chunk_markets_for_prompt(
        markets,
        "100",
        token_budget=6000,
        output_token_budget=3000,
        max_markets_per_chunk=8,
        n_ctx=8192,
    )
    current = chunk_markets_for_prompt(
        markets,
        "100",
        token_budget=7000,
        output_token_budget=4096,
        max_markets_per_chunk=24,
        n_ctx=12288,
    )
    assert len(current) < len(legacy)


def test_joint_n_ctx_budget_splits_overflow_chunk() -> None:
    markets = [_market(str(i)) for i in range(10)]
    chunks = chunk_markets_for_prompt(
        markets,
        "100",
        token_budget=100000,
        output_token_budget=100000,
        max_markets_per_chunk=100,
        n_ctx=500,
        context_safety_margin=0,
    )
    assert len(chunks) > 1
    assert all(len(c) < len(markets) for c in chunks)
