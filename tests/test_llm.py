"""Tests for LLM base class retry and token override behavior."""

from __future__ import annotations

import json

import pytest

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM, LLMInferenceError
from oddsgraph.schema import GraphFragment


class _StubGraphLLM(BaseGraphLLM):
    def __init__(self, settings: Settings, responses: list[str]) -> None:
        super().__init__(settings)
        self._responses = list(responses)
        self.max_tokens_seen: list[int] = []

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self.max_tokens_seen.append(max_tokens)
        if not self._responses:
            raise RuntimeError("no more stub responses")
        return self._responses.pop(0)


def _valid_fragment_json() -> str:
    return json.dumps(
        {
            "nodes": [
                {
                    "local_id": "team:brazil",
                    "type": "TEAM",
                    "label": "Brazil",
                    "aliases": [],
                    "confidence": 0.9,
                    "evidence_market_ids": ["m1"],
                }
            ],
            "edges": [],
        }
    )


def test_generate_fragment_retries_then_succeeds(tmp_path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path
    settings.failed_fragments_dir = tmp_path / "failed"
    llm = _StubGraphLLM(settings, ["not-json", _valid_fragment_json()])
    fragment = llm.generate_fragment("prompt", "evt1")
    assert len(fragment.nodes) == 1
    assert fragment.nodes[0].label == "Brazil"


def test_generate_fragment_uses_max_tokens_override(tmp_path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path
    settings.failed_fragments_dir = tmp_path / "failed"
    llm = _StubGraphLLM(settings, [_valid_fragment_json()])
    llm.generate_fragment("prompt", "evt1", max_tokens_override=512)
    assert llm.max_tokens_seen == [512]


def test_generate_fragment_raises_after_retries(tmp_path) -> None:
    settings = Settings()
    settings.build_dir = tmp_path
    settings.failed_fragments_dir = tmp_path / "failed"
    settings.max_retries = 1
    llm = _StubGraphLLM(settings, ["not-json", "still-not-json"])
    with pytest.raises(LLMInferenceError):
        llm.generate_fragment("prompt", "evt1")
    failed = settings.failed_fragments_dir / "evt1.txt"
    assert failed.exists()


def test_filter_llm_fragment_removes_disallowed_types(tmp_path) -> None:
    settings = Settings()
    fragment = GraphFragment.model_validate(
        json.loads(
            json.dumps(
                {
                    "nodes": [
                        {
                            "local_id": "team:brazil",
                            "type": "TEAM",
                            "label": "Brazil",
                            "aliases": [],
                            "confidence": 0.9,
                            "evidence_market_ids": ["m1"],
                        },
                        {
                            "local_id": "market:m1",
                            "type": "MARKET",
                            "label": "Market",
                            "aliases": [],
                            "confidence": 1.0,
                            "evidence_market_ids": ["m1"],
                        },
                    ],
                    "edges": [
                        {
                            "source": "market:m1",
                            "target": "team:brazil",
                            "type": "PRICES",
                            "confidence": 0.8,
                            "evidence_market_ids": ["m1"],
                            "evidence_text": "price",
                        }
                    ],
                }
            )
        )
    )
    llm = _StubGraphLLM(settings, [])
    filtered = llm._filter_llm_fragment(fragment)
    assert len(filtered.nodes) == 1
    assert filtered.nodes[0].type.value == "TEAM"
    assert filtered.edges == []
