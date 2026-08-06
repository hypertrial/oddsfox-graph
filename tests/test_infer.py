"""Regression tests for infer resume and error isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oddsgraph.config import Settings
from oddsgraph.infer import (
    _chunk_manifest_path,
    _fragment_path,
    _part_fragment_path,
    _verified_fragment_path,
    infer_event_fragments,
)
from oddsgraph.llm import BaseGraphLLM
from oddsgraph.ontology import NodeType
from oddsgraph.paths import sanitize_event_id_for_path
from oddsgraph.reporting import load_inference_report
from oddsgraph.schema import GraphFragment, Node, SemanticMarket


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    settings.resume = False
    settings.deterministic_topology = False
    settings.use_few_shot_exemplars = False
    settings.llm_backend = "server"
    settings.llm_concurrency = 2
    settings.chunk_token_budget = 2000
    settings.chunk_output_token_budget = 2000
    settings.max_markets_per_chunk = 1
    settings.n_ctx = 8000
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


def test_verify_deterministic_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.deterministic_topology = True
    settings.verify_deterministic = False
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="match-evt",
            event_title="Brazil vs. Morocco - Exact Score",
            event_slug="fifwc-bra-mar-2026-06-14",
            question="Winner?",
            outcomes=["Brazil", "Morocco"],
            sports_market_type="soccer_match",
        )
    ]

    class ShouldNotCall(BaseGraphLLM):
        def _complete(self, user_prompt: str, max_tokens: int, temperature: float) -> str:
            raise AssertionError("LLM should not be called when verify is off")

    results = infer_event_fragments(settings, markets, llm=ShouldNotCall(settings))
    assert results == {}
    report = load_inference_report(settings.inference_report_path)
    assert report.per_event_status["match-evt"] == "deterministic"


def test_verify_deterministic_marks_verified_and_corrected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.deterministic_topology = True
    settings.verify_deterministic = True
    settings.llm_backend = "inprocess"
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="match-evt",
            event_title="Brazil vs. Morocco - Exact Score",
            event_slug="fifwc-bra-mar-2026-06-14",
            question="Winner?",
            outcomes=["Brazil", "Morocco"],
            sports_market_type="soccer_match",
        )
    ]

    class EchoVerifyLLM(BaseGraphLLM):
        def __init__(self, settings: Settings, *, mutate: bool) -> None:
            super().__init__(settings)
            self.mutate = mutate

        def _complete(
            self, user_prompt: str, max_tokens: int, temperature: float
        ) -> str:
            # Return a minimal TEAM node; mutate path adds an extra alias-like label.
            label = "BrazilX" if self.mutate else "Brazil"
            return json.dumps(
                {
                    "n": [
                        {
                            "id": f"team:{label.lower()}",
                            "t": "TEAM",
                            "l": label,
                            "a": [],
                            "c": 0.9,
                            "e": ["m1"],
                        }
                    ],
                    "g": [],
                }
            )

    # Corrected path: LLM returns a different fragment than deterministic topology.
    results = infer_event_fragments(
        settings, markets, llm=EchoVerifyLLM(settings, mutate=True)
    )
    assert "match-evt" in results
    report = load_inference_report(settings.inference_report_path)
    assert report.per_event_status["match-evt"] in {
        "deterministic_corrected",
        "deterministic_verified",
    }
    assert (settings.fragments_dir / "match-evt__verified.json").exists()


def test_verify_deterministic_resume_skips_llm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.deterministic_topology = True
    settings.verify_deterministic = True
    settings.resume = True
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="match-evt",
            event_title="Brazil vs. Morocco - Exact Score",
            event_slug="fifwc-bra-mar-2026-06-14",
            question="Winner?",
            outcomes=["Brazil", "Morocco"],
            sports_market_type="soccer_match",
        )
    ]

    class CountingLLM(BaseGraphLLM):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.calls = 0

        def _complete(
            self, user_prompt: str, max_tokens: int, temperature: float
        ) -> str:
            self.calls += 1
            return json.dumps(
                {
                    "n": [
                        {
                            "id": "team:brazil",
                            "t": "TEAM",
                            "l": "Brazil",
                            "a": [],
                            "c": 0.9,
                            "e": ["m1"],
                        }
                    ],
                    "g": [],
                }
            )

    llm = CountingLLM(settings)
    infer_event_fragments(settings, markets, llm=llm)
    first_calls = llm.calls
    assert first_calls >= 1
    assert (settings.fragments_dir / "match-evt__verified.json").exists()

    llm2 = CountingLLM(settings)
    results = infer_event_fragments(settings, markets, llm=llm2)
    assert llm2.calls == 0
    assert "match-evt" in results


def test_fragments_equal_considers_aliases(tmp_path: Path) -> None:
    from oddsgraph.infer import _fragments_equal

    a = GraphFragment(
        nodes=[
            Node(
                local_id="team:brazil",
                type=NodeType.TEAM,
                label="Brazil",
                aliases=["Selecao"],
                confidence=1.0,
                evidence_market_ids=["m1"],
            )
        ]
    )
    b = GraphFragment(
        nodes=[
            Node(
                local_id="team:brazil",
                type=NodeType.TEAM,
                label="Brazil",
                aliases=[],
                confidence=1.0,
                evidence_market_ids=["m1"],
            )
        ]
    )
    assert not _fragments_equal(a, b)


@pytest.mark.parametrize(
    "event_id",
    [
        "../escape",
        "nested/path",
        "nested\\path",
        "",
        ".",
        "..",
        "has space",
        "O'Brien",
    ],
)
def test_fragment_paths_reject_unsafe_event_ids(
    tmp_path: Path, event_id: str
) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="Unsafe event_id"):
        _fragment_path(settings, event_id)
    with pytest.raises(ValueError, match="Unsafe event_id"):
        _part_fragment_path(settings, event_id, 0)
    with pytest.raises(ValueError, match="Unsafe event_id"):
        _chunk_manifest_path(settings, event_id)
    with pytest.raises(ValueError, match="Unsafe event_id"):
        _verified_fragment_path(settings, event_id)


def test_fragment_paths_stay_under_fragments_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    event_id = "12345"
    assert sanitize_event_id_for_path(event_id) == event_id
    path = _fragment_path(settings, event_id)
    assert path.parent == settings.fragments_dir
    assert path.name == "12345.json"
    assert path.resolve().is_relative_to(settings.fragments_dir.resolve())
