"""Tests for few-shot exemplars and verification prompts."""

from __future__ import annotations

from oddsgraph.ontology import NodeType
from oddsgraph.prompts import (
    build_event_prompt,
    build_verification_prompt,
    load_exemplars,
    select_exemplars,
)
from oddsgraph.schema import GraphFragment, Node, SemanticMarket


def test_load_exemplars_nonempty() -> None:
    exemplars = load_exemplars()
    assert len(exemplars) >= 3
    assert "fragment" in exemplars[0]


def test_select_exemplars_ranks_similar_titles() -> None:
    exemplars = [
        {
            "event_title": "World Cup Golden Ball Winner",
            "question": "Who wins Golden Ball?",
            "fragment": {"n": [], "g": []},
        },
        {
            "event_title": "Brazil vs. Morocco - Exact Score",
            "question": "Exact score",
            "fragment": {"n": [], "g": []},
        },
        {
            "event_title": "World Cup Group D Winner",
            "question": "Group D",
            "fragment": {"n": [], "g": []},
        },
    ]
    selected = select_exemplars(
        "Golden Ball award",
        "Will Messi win Golden Ball?",
        exemplars=exemplars,
        top_k=1,
    )
    assert len(selected) == 1
    assert "Golden Ball" in selected[0]["event_title"]


def test_build_event_prompt_includes_few_shot_when_provided() -> None:
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="e1",
            event_title="World Cup Golden Ball Winner",
            question="Will Brazil win?",
            outcomes=["Yes", "No"],
        )
    ]
    exemplars = load_exemplars()[:1]
    prompt = build_event_prompt("e1", markets, few_shot_exemplars=exemplars)
    assert "Few-shot examples" in prompt
    assert "CompactGraphFragment" in prompt


def test_build_event_prompt_excludes_few_shot_when_empty() -> None:
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="e1",
            event_title="Title",
            question="Q",
            outcomes=["Yes", "No"],
        )
    ]
    prompt = build_event_prompt("e1", markets, few_shot_exemplars=[])
    assert "Few-shot examples" not in prompt


def test_build_verification_prompt_includes_candidate() -> None:
    markets = [
        SemanticMarket(
            market_id="m1",
            event_id="e1",
            event_title="Brazil vs. Morocco",
            question="Winner?",
            outcomes=["Brazil", "Morocco"],
        )
    ]
    candidate = GraphFragment(
        nodes=[
            Node(
                local_id="team:brazil",
                type=NodeType.TEAM,
                label="Brazil",
                confidence=1.0,
                evidence_market_ids=["m1"],
            )
        ],
        edges=[],
    )
    prompt = build_verification_prompt("e1", markets, candidate)
    assert "candidate_fragment" in prompt
    assert "team:brazil" in prompt
    assert '"n"' in prompt
