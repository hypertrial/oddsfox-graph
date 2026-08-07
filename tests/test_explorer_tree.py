"""Tests for mirrored knockout tree construction."""

from __future__ import annotations

from oddsgraph.explorer.tree import (
    BracketHalf,
    build_knockout_tree,
    compute_ripple,
)


def _match(
    match_id: str,
    stage: str,
    *,
    fifa: int | None = None,
    label: str | None = None,
    classes: str = "MATCH",
    **extra: object,
) -> dict:
    aliases = [f"fifa-match-{fifa}"] if fifa is not None else []
    data = {
        "id": match_id,
        "type": "MATCH",
        "stage": stage,
        "label": label or match_id,
        "aliases": aliases,
        **extra,
    }
    return {"data": data, "classes": classes}


def _edge(source: str, target: str) -> dict:
    return {
        "data": {
            "id": f"{source}|ADVANCES_TO|{target}",
            "source": source,
            "target": target,
            "edge_type": "ADVANCES_TO",
        },
        "classes": "ADVANCES_TO",
    }


def _full_bracket_elements() -> list[dict]:
    """Minimal 16-team half-pair structure: 4 R32 → 2 R16 → 1 QF → 1 SF per half."""
    # Left half: r32 1-4 → r16 10-11 → qf 20 → sf 30
    # Right half: r32 5-8 → r16 12-13 → qf 21 → sf 31
    elements = [
        _match("l-r32-1", "Round of 32", fifa=1),
        _match("l-r32-2", "Round of 32", fifa=2),
        _match("l-r32-3", "Round of 32", fifa=3),
        _match("l-r32-4", "Round of 32", fifa=4),
        _match("r-r32-5", "Round of 32", fifa=5),
        _match("r-r32-6", "Round of 32", fifa=6),
        _match("r-r32-7", "Round of 32", fifa=7),
        _match("r-r32-8", "Round of 32", fifa=8),
        _match("l-r16-1", "Round of 16", fifa=10),
        _match("l-r16-2", "Round of 16", fifa=11),
        _match("r-r16-1", "Round of 16", fifa=12),
        _match("r-r16-2", "Round of 16", fifa=13),
        _match("l-qf", "Quarterfinals", fifa=20),
        _match("r-qf", "Quarterfinals", fifa=21),
        _match("l-sf", "Semifinals", fifa=30),
        _match("r-sf", "Semifinals", fifa=31),
        _match("final", "Final", fifa=100, home_team="A", away_team="B", current_home_prob=0.6),
        _match("third", "Third Place", fifa=99),
        _edge("l-r32-1", "l-r16-1"),
        _edge("l-r32-2", "l-r16-1"),
        _edge("l-r32-3", "l-r16-2"),
        _edge("l-r32-4", "l-r16-2"),
        _edge("r-r32-5", "r-r16-1"),
        _edge("r-r32-6", "r-r16-1"),
        _edge("r-r32-7", "r-r16-2"),
        _edge("r-r32-8", "r-r16-2"),
        _edge("l-r16-1", "l-qf"),
        _edge("l-r16-2", "l-qf"),
        _edge("r-r16-1", "r-qf"),
        _edge("r-r16-2", "r-qf"),
        _edge("l-qf", "l-sf"),
        _edge("r-qf", "r-sf"),
        _edge("l-sf", "final"),
        _edge("r-sf", "final"),
    ]
    return elements


def test_build_knockout_tree_splits_left_right_halves() -> None:
    tree = build_knockout_tree(_full_bracket_elements())
    assert isinstance(tree.left, BracketHalf)
    assert tree.final is not None
    assert tree.third_place is not None
    assert [m["data"]["id"] for m in tree.left.sf] == ["l-sf"]
    assert [m["data"]["id"] for m in tree.right.sf] == ["r-sf"]
    assert len(tree.left.r32) == 4
    assert len(tree.right.r32) == 4
    assert len(tree.left.r16) == 2
    assert len(tree.right.r16) == 2
    assert len(tree.left.qf) == 1
    assert len(tree.right.qf) == 1
    left_ids = {m["data"]["id"] for m in tree.left.r32}
    right_ids = {m["data"]["id"] for m in tree.right.r32}
    assert left_ids == {"l-r32-1", "l-r32-2", "l-r32-3", "l-r32-4"}
    assert right_ids == {"r-r32-5", "r-r32-6", "r-r32-7", "r-r32-8"}


def test_build_knockout_tree_orders_feeders_by_fifa_id() -> None:
    tree = build_knockout_tree(_full_bracket_elements())
    assert [m["data"]["id"] for m in tree.left.r32] == [
        "l-r32-1",
        "l-r32-2",
        "l-r32-3",
        "l-r32-4",
    ]
    # Lower FIFA SF id becomes left half.
    assert tree.left.sf[0]["data"]["id"] == "l-sf"
    assert tree.right.sf[0]["data"]["id"] == "r-sf"


def test_build_knockout_tree_champion_from_prob() -> None:
    tree = build_knockout_tree(_full_bracket_elements())
    assert tree.champion == "A"


def test_build_knockout_tree_fallback_without_final_feeders() -> None:
    elements = [
        _match("a", "Round of 32", fifa=1),
        _match("b", "Round of 32", fifa=2),
        _match("c", "Round of 32", fifa=3),
        _match("d", "Round of 32", fifa=4),
        _match("sf1", "Semifinals", fifa=10),
        _match("sf2", "Semifinals", fifa=11),
        _match("third", "Third Place", fifa=99),
    ]
    tree = build_knockout_tree(elements)
    assert tree.final is None
    assert len(tree.left.r32) + len(tree.right.r32) == 4
    assert len(tree.left.sf) + len(tree.right.sf) == 2


def test_compute_ripple_empty_without_just_finished() -> None:
    tree = build_knockout_tree(_full_bracket_elements())
    ripple = compute_ripple(tree)
    assert ripple.active_pairs == {}
    assert ripple.target_ids == frozenset()


def test_compute_ripple_single_r32_finish() -> None:
    elements = _full_bracket_elements()
    for el in elements:
        if el.get("data", {}).get("id") == "l-r32-1":
            el["data"]["just_finished"] = True
            break
    tree = build_knockout_tree(elements)
    ripple = compute_ripple(tree)
    assert ripple.active_pairs.get("left:r32") == frozenset({0})
    assert "l-r16-1" in ripple.target_ids
    assert "final" not in ripple.target_ids


def test_compute_ripple_simultaneous_finishes() -> None:
    elements = _full_bracket_elements()
    for el in elements:
        mid = el.get("data", {}).get("id")
        if mid in {"l-r32-1", "l-r32-2", "r-r32-5"}:
            el["data"]["just_finished"] = True
    tree = build_knockout_tree(elements)
    ripple = compute_ripple(tree)
    assert ripple.active_pairs.get("left:r32") == frozenset({0})
    assert ripple.active_pairs.get("right:r32") == frozenset({0})
    assert {"l-r16-1", "r-r16-1"} <= set(ripple.target_ids)


def test_compute_ripple_semifinal_targets_final_and_third() -> None:
    elements = _full_bracket_elements()
    for el in elements:
        if el.get("data", {}).get("id") == "l-sf":
            el["data"]["just_finished"] = True
            break
    tree = build_knockout_tree(elements)
    ripple = compute_ripple(tree)
    assert ripple.active_pairs.get("left:sf") == frozenset({0})
    assert {"final", "third"} <= set(ripple.target_ids)
