"""Pure canvas filter/merge helpers for the graph explorer (no Dash dependency)."""

from __future__ import annotations

from typing import Any

from oddsgraph.explorer.presentation import (
    PRESERVED_CLASSES,
    is_stage_header,
    merge_class_sets,
    split_classes,
)


def element_id(el: dict[str, Any]) -> str | None:
    data = el.get("data") or {}
    return data.get("id")


def is_edge(el: dict[str, Any]) -> bool:
    data = el.get("data") or {}
    return "source" in data and "target" in data


def is_node(el: dict[str, Any]) -> bool:
    return not is_edge(el)


def merge_elements(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate Cytoscape elements by id, preserving interaction classes."""
    merged: dict[str, dict[str, Any]] = {}
    for el in current or []:
        eid = element_id(el)
        if eid is not None:
            merged[eid] = el
    for el in incoming:
        eid = element_id(el)
        if eid is None:
            continue
        existing = merged.get(eid)
        if existing is not None:
            _, preserved = split_classes(existing.get("classes"))
            semantic, _ = split_classes(el.get("classes"))
            # Keep prior interaction/visibility classes; take new semantic type.
            combined = merge_class_sets(semantic, preserved)
            el = {**el, "classes": combined}
            # Preserve preset positions when the incoming element lacks them.
            if "position" in existing and "position" not in el:
                el = {**el, "position": existing["position"]}
        merged[eid] = el
    return list(merged.values())


def node_types_in_elements(elements: list[dict[str, Any]]) -> list[str]:
    """Return sorted unique node ``type`` values present in ``elements``."""
    types: set[str] = set()
    for el in elements:
        if not is_node(el) or is_stage_header(el):
            continue
        node_type = (el.get("data") or {}).get("type")
        if node_type:
            types.add(str(node_type))
    return sorted(types)


def apply_filters(
    elements: list[dict[str, Any]],
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
) -> list[dict[str, Any]]:
    """Hide nodes/edges that fail type, confidence, or inference_method filters.

    An empty ``visible_types`` list hides all nodes (and therefore all edges).
    ``None`` is treated the same as an empty list.

    Preserves non-hidden interaction classes (``path-active``, ``path-muted``,
    ``hovered``) across filter passes.
    """
    visible = set(visible_types or [])
    method = (inference_method or "").strip()
    filtered: list[dict[str, Any]] = []

    for el in elements:
        data = el.get("data") or {}
        semantic, preserved = split_classes(el.get("classes"))
        preserved.discard("hidden")
        confidence = float(data.get("confidence") or 0.0)
        el_method = str(data.get("inference_method") or "")
        hide = False

        if is_stage_header(el):
            # Column labels stay visible regardless of MATCH-only type filters.
            filtered.append({**el, "classes": merge_class_sets(semantic, preserved)})
            continue

        if confidence < min_confidence:
            hide = True
        if method and el_method != method:
            hide = True
        if is_node(el):
            node_type = str(data.get("type") or "")
            if node_type not in visible:
                hide = True

        if hide:
            preserved.add("hidden")
        filtered.append({**el, "classes": merge_class_sets(semantic, preserved)})

    visible_node_ids: set[str] = set()
    for el in filtered:
        if not is_node(el):
            continue
        _, preserved = split_classes(el.get("classes"))
        if "hidden" in preserved:
            continue
        eid = element_id(el)
        if eid is not None:
            visible_node_ids.add(eid)

    result: list[dict[str, Any]] = []
    for el in filtered:
        if is_node(el):
            result.append(el)
            continue
        data = el.get("data") or {}
        semantic, preserved = split_classes(el.get("classes"))
        source = data.get("source")
        target = data.get("target")
        if source not in visible_node_ids or target not in visible_node_ids:
            preserved.add("hidden")
        result.append({**el, "classes": merge_class_sets(semantic, preserved)})
    return result


def union_types(current: list[str] | None, extra: list[str]) -> list[str]:
    """Return ``current`` extended with any missing types from ``extra`` (stable order)."""
    merged = list(current or [])
    seen = set(merged)
    for item in extra:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def clear_interaction_classes(
    elements: list[dict[str, Any]],
    *,
    keep_hidden: bool = True,
) -> list[dict[str, Any]]:
    """Strip path/hover classes; optionally keep ``hidden``."""
    result: list[dict[str, Any]] = []
    for el in elements:
        semantic, preserved = split_classes(el.get("classes"))
        keep: set[str] = set()
        if keep_hidden and "hidden" in preserved:
            keep.add("hidden")
        # Drop everything else in PRESERVED_CLASSES.
        for token in list(preserved):
            if token not in PRESERVED_CLASSES:
                keep.add(token)
        result.append({**el, "classes": merge_class_sets(semantic, keep)})
    return result
