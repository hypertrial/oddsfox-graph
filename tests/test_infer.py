"""Regression tests for infer resume and error isolation."""

from __future__ import annotations

import json
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.infer import infer_event_fragments
from oddsgraph.llm import BaseGraphLLM
from oddsgraph.ontology import NodeType
from oddsgraph.reporting import load_inference_report
from oddsgraph.schema import GraphFragment, Node, SemanticMarket


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    settings.resume = False
    settings.deterministic_topology = False
    settings.llm_backend = "server"
    settings.llm_concurrency = 2
    settings.chunk_token_budget = 500
    settings.chunk_output_token_budget = 500
    settings.max_markets_per_chunk = 1
    settings.n_ctx = 2000
    return settings


def _prop_market(market_id: str, event_id: str) -> SemanticMarket:
    return SemanticMarket(
        market_id=market_id,
        event_id=event_id,
        event_title="Golden Ball Winner",
        question=f"Question {market_id}",
        outcomes=["Yes", "No"],
    )


class _LabelLLM(BaseGraphLLM):
    def __init__(self, settings: Settings, labels: list[str]) -> None:
        super().__init__(settings)
        self.labels = labels
        self._i = 0

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        label = self.labels[self._i % len(self.labels)]
        self._i += 1
        return json.dumps(
            {
                "nodes": [
                    {
                        "local_id": f"team:{label.lower()}",
                        "type": "TEAM",
                        "label": label,
                        "aliases": [],
                        "confidence": 0.9,
                        "evidence_market_ids": ["m"],
                    }
                ],
                "edges": [],
            }
        )


def test_infer_continues_after_non_llm_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class FailFirstLLM(BaseGraphLLM):
        def _complete(
            self,
            user_prompt: str,
            max_tokens: int,
            temperature: float,
        ) -> str:
            raise AssertionError("generate_fragment should short-circuit")

        def generate_fragment(
            self,
            prompt: str,
            event_id: str,
            max_tokens_override: int | None = None,
        ) -> GraphFragment:
            if event_id == "boom":
                raise RuntimeError("llama-server chat completion failed (500): boom")
            return GraphFragment(
                nodes=[
                    Node(
                        local_id="team:ok",
                        type=NodeType.TEAM,
                        label="OK",
                        confidence=0.9,
                        evidence_market_ids=["1"],
                    )
                ]
            )

    markets = [
        _prop_market("b1", "boom"),
        _prop_market("o1", "ok"),
    ]
    results = infer_event_fragments(settings, markets, llm=FailFirstLLM(settings))
    assert "boom" not in results
    assert "ok" in results
    report = load_inference_report(settings.inference_report_path)
    assert report.per_event_status["boom"] == "failed"
    assert report.per_event_status["ok"] == "success"


def test_infer_clears_stale_part_fragments_when_manifest_mismatches(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.resume = True
    settings.max_markets_per_chunk = 1
    markets = [_prop_market(f"m{i}", "e1") for i in range(4)]

    first = infer_event_fragments(
        settings, markets, llm=_LabelLLM(settings, ["T0", "T1", "T2", "T3"])
    )
    assert sorted(n.label for n in first["e1"].nodes) == ["T0", "T1", "T2", "T3"]
    (settings.fragments_dir / "e1.json").unlink()

    settings.max_markets_per_chunk = 2
    second = infer_event_fragments(
        settings, markets, llm=_LabelLLM(settings, ["A0", "A1"])
    )
    assert sorted(n.label for n in second["e1"].nodes) == ["A0", "A1"]
    assert not list(settings.fragments_dir.glob("e1__part2.json"))
    assert not list(settings.fragments_dir.glob("e1__part3.json"))
