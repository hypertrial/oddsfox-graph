"""Pure canvas mutation helpers for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import Output, no_update

from oddsgraph.config import Settings
from oddsgraph.explorer import TOPOLOGY_NODE_TYPES, VIEW_BRACKET, VIEW_TOPOLOGY
from oddsgraph.explorer.data import (
    GraphSlice,
    bracket_elements,
    get_node,
    node_element,
    node_neighbors,
    topology_elements,
)
from oddsgraph.explorer.filters import (
    apply_filters,
    clear_interaction_classes,
    is_edge,
    merge_elements,
    node_types_in_elements,
    union_types,
)
from oddsgraph.explorer.presentation import apply_path_highlight, stylesheet_for

CanvasMutation = tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any]


def _noop_mutation() -> CanvasMutation:
    return (
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def _truncation_note(slice_: GraphSlice, limit: int = 300) -> str:
    if slice_.truncated:
        return f" Truncated to {limit} incident edges."
    return ""


def _view_payload(
    settings: Settings, view_mode: str
) -> tuple[GraphSlice, list[str], str, str]:
    """Return (slice, type_filter, layout_name, status) for a view mode."""
    if view_mode == VIEW_TOPOLOGY:
        return (
            topology_elements(settings),
            sorted(TOPOLOGY_NODE_TYPES),
            "breadthfirst",
            "Loaded full topology view.",
        )
    return (
        bracket_elements(settings),
        ["MATCH"],
        "preset",
        "Loaded knockout bracket view.",
    )


def highlight_on_tap(
    elements: list[dict[str, Any]] | None,
    tap_node: dict[str, Any] | None,
) -> CanvasMutation | None:
    """Apply path highlight for a tapped node. Returns None if tap is empty."""
    if not tap_node or not tap_node.get("id"):
        return None
    focus_id = str(tap_node["id"])
    highlighted = apply_path_highlight(elements or [], focus_id)
    return (
        highlighted,
        f"Highlighted path through {focus_id}.",
        no_update,
        no_update,
        no_update,
        no_update,
        focus_id,
        None,
        no_update,
    )


def filter_canvas(
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Re-apply type/confidence/inference filters without reloading the graph."""
    return (
        apply_filters(elements or [], visible_types, min_confidence, inference_method),
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def load_view(
    settings: Settings,
    view_mode: str,
    min_confidence: float,
    inference_method: str,
    *,
    skip_view_reload: bool = False,
    reset: bool = False,
) -> CanvasMutation:
    """Load bracket/topology view, honoring skip-view-reload on mode echo."""
    if not reset and skip_view_reload:
        return (
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            False,
        )
    slice_, next_types, layout_name, status = _view_payload(settings, view_mode)
    if reset:
        status = f"Reset to {view_mode} view."
    return (
        apply_filters(slice_.to_elements(), next_types, min_confidence, inference_method),
        status,
        next_types,
        layout_name,
        no_update,
        stylesheet_for(view_mode),
        None,
        None,
        False,
    )


def open_in_topology(
    settings: Settings,
    selected_node_id: str | None,
    min_confidence: float,
    inference_method: str,
    *,
    current_view_mode: str = VIEW_BRACKET,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Merge selected node + neighbors into full topology and switch view."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node first.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    topo = topology_elements(settings)
    row = get_node(settings, selected_node_id)
    extras = GraphSlice(
        nodes=[node_element(row)] if row else [],
        edges=[],
    )
    neighbors = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
    merged = merge_elements(topo.to_elements(), extras.to_elements())
    merged = merge_elements(merged, neighbors.to_elements())
    next_types = union_types(
        sorted(TOPOLOGY_NODE_TYPES),
        node_types_in_elements(extras.nodes + neighbors.nodes),
    )
    highlighted = apply_path_highlight(
        apply_filters(merged, next_types, min_confidence, inference_method),
        selected_node_id,
    )
    status = (
        f"Opened {selected_node_id} in Full topology."
        f"{_truncation_note(neighbors, neighbor_limit)}"
    )
    # Only suppress the view-mode echo reload when we actually change modes.
    skip_reload = current_view_mode != VIEW_TOPOLOGY
    return (
        highlighted,
        status,
        next_types,
        "breadthfirst",
        VIEW_TOPOLOGY,
        stylesheet_for(VIEW_TOPOLOGY),
        selected_node_id,
        None,
        skip_reload,
    )


def expand_neighbors(
    settings: Settings,
    selected_node_id: str | None,
    view_mode: str,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
    *,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Expand 1-hop neighbors; bracket view switches to topology first."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node before expanding.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    if view_mode == VIEW_BRACKET:
        topo = topology_elements(settings)
        neighbors = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
        merged = merge_elements(topo.to_elements(), neighbors.to_elements())
        next_types = union_types(
            sorted(TOPOLOGY_NODE_TYPES),
            node_types_in_elements(neighbors.nodes),
        )
        status = (
            f"Expanded {selected_node_id} in Full topology "
            f"(bracket view stays MATCH-only)."
            f"{_truncation_note(neighbors, neighbor_limit)}"
        )
        return (
            apply_filters(merged, next_types, min_confidence, inference_method),
            status,
            next_types,
            "breadthfirst",
            VIEW_TOPOLOGY,
            stylesheet_for(VIEW_TOPOLOGY),
            selected_node_id,
            None,
            True,
        )
    slice_ = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
    if not slice_.edges and len(slice_.nodes) <= 1:
        return (
            no_update,
            (
                f"No neighbors for {selected_node_id}. "
                "Covered templates bridge via REFERS_TO/PRICES; residual "
                "market types may still lack a topology bridge."
            ),
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    merged = merge_elements(elements, slice_.to_elements())
    next_types = union_types(visible_types, node_types_in_elements(slice_.nodes))
    status = (
        f"Expanded neighbors of {selected_node_id}."
        f"{_truncation_note(slice_, neighbor_limit)}"
    )
    return (
        apply_filters(merged, next_types, min_confidence, inference_method),
        status,
        next_types,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def remove_from_canvas(
    selected_node_id: str | None,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Remove the selected node and its incident edges from the canvas."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node before removing.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    remaining: list[dict[str, Any]] = []
    for el in elements or []:
        data = el.get("data") or {}
        eid = data.get("id")
        if eid == selected_node_id:
            continue
        if is_edge(el) and (
            data.get("source") == selected_node_id
            or data.get("target") == selected_node_id
        ):
            continue
        remaining.append(el)
    cleaned = clear_interaction_classes(
        apply_filters(remaining, visible_types, min_confidence, inference_method),
        keep_hidden=True,
    )
    return (
        cleaned,
        f"Removed {selected_node_id} from canvas.",
        no_update,
        no_update,
        no_update,
        no_update,
        None,
        None,
        no_update,
    )


def add_search_result(
    settings: Settings,
    node_id: str,
    view_mode: str,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
    *,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Add a search-hit node (and neighbors); bracket switches to topology."""
    row = get_node(settings, node_id)
    if row is None:
        return (
            no_update,
            f"Node not found: {node_id}",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    if view_mode == VIEW_BRACKET:
        topo = topology_elements(settings)
        slice_ = GraphSlice(nodes=[node_element(row)], edges=[])
        neighbors = node_neighbors(settings, node_id, limit=neighbor_limit)
        merged = merge_elements(topo.to_elements(), slice_.to_elements())
        merged = merge_elements(merged, neighbors.to_elements())
        next_types = union_types(
            sorted(TOPOLOGY_NODE_TYPES),
            node_types_in_elements(slice_.nodes + neighbors.nodes),
        )
        status = (
            f"Opened {node_id} in Full topology."
            f"{_truncation_note(neighbors, neighbor_limit)}"
        )
        return (
            apply_filters(merged, next_types, min_confidence, inference_method),
            status,
            next_types,
            "breadthfirst",
            VIEW_TOPOLOGY,
            stylesheet_for(VIEW_TOPOLOGY),
            node_id,
            None,
            True,
        )
    slice_ = GraphSlice(nodes=[node_element(row)], edges=[])
    neighbors = node_neighbors(settings, node_id, limit=neighbor_limit)
    merged = merge_elements(elements, slice_.to_elements())
    merged = merge_elements(merged, neighbors.to_elements())
    added_types = node_types_in_elements(slice_.nodes + neighbors.nodes)
    next_types = union_types(visible_types, added_types)
    status = (
        f"Added {node_id} and its neighbors to the canvas."
        f"{_truncation_note(neighbors, neighbor_limit)}"
    )
    return (
        apply_filters(merged, next_types, min_confidence, inference_method),
        status,
        next_types,
        no_update,
        no_update,
        no_update,
        node_id,
        None,
        no_update,
    )


def _canvas_callback_outputs(*, allow_duplicate: bool = False) -> list[Output]:
    kwargs = {"allow_duplicate": True} if allow_duplicate else {}
    return [
        Output("graph-cyto", "elements", **kwargs),
        Output("action-status", "children", **kwargs),
        Output("type-filter", "value", **kwargs),
        Output("layout-dropdown", "value", **kwargs),
        Output("view-mode", "value", **kwargs),
        Output("graph-cyto", "stylesheet", **kwargs),
        Output("selected-node-id", "data", allow_duplicate=True),
        Output("selected-edge-id", "data", allow_duplicate=True),
        Output("skip-view-reload", "data", **kwargs),
    ]

