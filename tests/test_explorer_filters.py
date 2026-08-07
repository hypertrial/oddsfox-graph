"""Tests for canvas filter helpers (no Dash dependency)."""

from __future__ import annotations

from oddsgraph.explorer.filters import (
    apply_filters,
    merge_elements,
    node_types_in_elements,
    union_types,
)


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


def test_merge_and_union_helpers() -> None:
    current = [_node("team:a", "TEAM")]
    current[0]["classes"] = "TEAM hidden"
    incoming = [_node("team:a", "TEAM"), _node("match:1", "MATCH")]
    merged = merge_elements(current, incoming)
    by_id = {el["data"]["id"]: el for el in merged}
    assert set(by_id) == {"team:a", "match:1"}
    assert "hidden" in by_id["team:a"]["classes"].split()
    assert "TEAM" in by_id["team:a"]["classes"].split()

    assert node_types_in_elements(merged) == ["MATCH", "TEAM"]
    assert union_types(["TEAM"], ["EVENT", "TEAM", "MARKET"]) == [
        "TEAM",
        "EVENT",
        "MARKET",
    ]


def test_merge_and_filters_preserve_path_classes() -> None:
    current = [_node("match:1", "MATCH")]
    current[0]["classes"] = "MATCH path-active"
    current[0]["position"] = {"x": 10, "y": 20}
    incoming = [_node("match:1", "MATCH")]
    merged = merge_elements(current, incoming)
    by_id = {el["data"]["id"]: el for el in merged}
    assert "path-active" in by_id["match:1"]["classes"].split()
    assert by_id["match:1"]["position"] == {"x": 10, "y": 20}

    elements = [
        {
            "data": {
                "id": "match:1",
                "type": "MATCH",
                "label": "A vs. B",
                "confidence": 1.0,
                "inference_method": "deterministic",
            },
            "classes": "MATCH path-active",
        },
        {
            "data": {
                "id": "match:2",
                "type": "MATCH",
                "label": "C vs. D",
                "confidence": 1.0,
                "inference_method": "deterministic",
            },
            "classes": "MATCH path-muted",
        },
        _edge("match:1", "match:2", "ADVANCES_TO"),
    ]
    elements[2]["classes"] = "ADVANCES_TO path-active"
    filtered = apply_filters(elements, ["MATCH"], min_confidence=0.0, inference_method="")
    by_id = {el["data"]["id"]: el for el in filtered}
    assert "path-active" in by_id["match:1"]["classes"].split()
    assert "path-muted" in by_id["match:2"]["classes"].split()
    assert "path-active" in by_id["match:1|ADVANCES_TO|match:2"]["classes"].split()
    assert "hidden" not in by_id["match:1"]["classes"].split()


def test_apply_filters_keeps_stage_headers_visible() -> None:
    from oddsgraph.explorer.presentation import bracket_stage_headers

    elements = [
        _node("match:1", "MATCH"),
        *bracket_stage_headers(columns={0}),
    ]
    filtered = apply_filters(elements, ["MATCH"], min_confidence=0.0, inference_method="")
    by_id = {el["data"]["id"]: el for el in filtered}
    assert "hidden" not in by_id["match:1"]["classes"].split()
    assert "hidden" not in by_id["stage-header:0"]["classes"].split()
    assert node_types_in_elements(elements) == ["MATCH"]

    # Even with an empty type filter, column headers stay visible.
    filtered_empty = apply_filters(
        elements, [], min_confidence=0.0, inference_method=""
    )
    by_id = {el["data"]["id"]: el for el in filtered_empty}
    assert "hidden" in by_id["match:1"]["classes"].split()
    assert "hidden" not in by_id["stage-header:0"]["classes"].split()


def test_clear_interaction_classes_keeps_hidden() -> None:
    from oddsgraph.explorer.filters import clear_interaction_classes

    elements = [
        {
            "data": {"id": "match:1", "type": "MATCH"},
            "classes": "MATCH path-active hidden",
        },
        {
            "data": {"id": "match:2", "type": "MATCH"},
            "classes": "MATCH path-muted",
        },
    ]
    cleared = clear_interaction_classes(elements, keep_hidden=True)
    by_id = {el["data"]["id"]: el for el in cleared}
    assert by_id["match:1"]["classes"].split() == ["MATCH", "hidden"]
    assert by_id["match:2"]["classes"].split() == ["MATCH"]
