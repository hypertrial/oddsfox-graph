"""Tests for canvas filter helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.explorer.filters import apply_filters


def _node(node_id: str, node_type: str, confidence: float = 1.0, method: str = "deterministic") -> dict:
    return {
        "data": {
            "id": node_id,
            "type": node_type,
            "label": node_id,
            "confidence": confidence,
            "inference_method": method,
        },
        "classes": node_type,
    }


def _edge(source: str, target: str, edge_type: str = "PART_OF", confidence: float = 1.0) -> dict:
    return {
        "data": {
            "id": f"{source}|{edge_type}|{target}",
            "source": source,
            "target": target,
            "edge_type": edge_type,
            "confidence": confidence,
            "inference_method": "deterministic",
        },
        "classes": edge_type,
    }


def test_apply_filters_empty_types_hides_all_nodes() -> None:
    elements = [
        _node("team:a", "TEAM"),
        _node("match:1", "MATCH"),
        _edge("team:a", "match:1", "PARTICIPATES_IN"),
    ]
    filtered = apply_filters(elements, [], min_confidence=0.0, inference_method="")
    assert all("hidden" in (el.get("classes") or "").split() for el in filtered)


def test_apply_filters_hides_unselected_types_and_dangling_edges() -> None:
    elements = [
        _node("team:a", "TEAM"),
        _node("event:1", "EVENT"),
        _edge("team:a", "event:1", "PRICES"),
    ]
    filtered = apply_filters(elements, ["TEAM"], min_confidence=0.0, inference_method="")
    by_id = {el["data"]["id"]: el for el in filtered}
    assert "hidden" not in by_id["team:a"]["classes"].split()
    assert "hidden" in by_id["event:1"]["classes"].split()
    assert "hidden" in by_id["team:a|PRICES|event:1"]["classes"].split()


def test_apply_filters_confidence_and_inference_method() -> None:
    elements = [
        _node("team:a", "TEAM", confidence=0.4, method="deterministic"),
        _node("team:b", "TEAM", confidence=0.9, method="llm"),
    ]
    filtered = apply_filters(
        elements,
        ["TEAM"],
        min_confidence=0.5,
        inference_method="deterministic",
    )
    by_id = {el["data"]["id"]: el for el in filtered}
    assert "hidden" in by_id["team:a"]["classes"].split()
    assert "hidden" in by_id["team:b"]["classes"].split()
