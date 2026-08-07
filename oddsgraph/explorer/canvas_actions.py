"""Pure canvas mutation helpers for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import Output, no_update

from oddsgraph.config import Settings
from oddsgraph.explorer.data import bracket_elements, stage_odds_by_team
from oddsgraph.explorer.filters import (
    apply_filters,
    clear_interaction_classes,
    is_edge,
)
from oddsgraph.explorer.presentation import (
    apply_path_highlight,
    apply_time_slice,
    bracket_stylesheet,
)

# elements, status, stylesheet, selected_node_id, selected_edge_id
CanvasMutation = tuple[Any, Any, Any, Any, Any]

_BRACKET_TYPES = ["MATCH"]


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
        focus_id,
        None,
    )


def filter_canvas(
    elements: list[dict[str, Any]] | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Re-apply confidence/inference filters without reloading the graph."""
    return (
        apply_filters(elements or [], _BRACKET_TYPES, min_confidence, inference_method),
        no_update,
        no_update,
        no_update,
        no_update,
    )


def load_view(
    settings: Settings,
    min_confidence: float,
    inference_method: str,
    *,
    hour_epoch: int | None = None,
    reset: bool = False,
) -> CanvasMutation:
    """Reload the knockout bracket view."""
    slice_ = bracket_elements(settings)
    elements = apply_time_slice(
        slice_.to_elements(),
        hour_epoch,
        stage_odds=stage_odds_by_team(settings),
    )
    status = "Reset knockout bracket view." if reset else "Loaded knockout bracket view."
    return (
        apply_filters(elements, _BRACKET_TYPES, min_confidence, inference_method),
        status,
        bracket_stylesheet(),
        None,
        None,
    )


def apply_time_slider(
    elements: list[dict[str, Any]] | None,
    hour_epoch: int | None,
    min_confidence: float,
    inference_method: str,
    *,
    settings: Settings | None = None,
) -> CanvasMutation:
    """Update projected teams and advance probabilities for the selected hour."""
    stage_odds = stage_odds_by_team(settings) if settings is not None else {}
    stamped = apply_time_slice(elements or [], hour_epoch, stage_odds=stage_odds)
    return (
        apply_filters(stamped, _BRACKET_TYPES, min_confidence, inference_method),
        no_update,
        no_update,
        no_update,
        no_update,
    )


def remove_from_canvas(
    selected_node_id: str | None,
    elements: list[dict[str, Any]] | None,
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
        apply_filters(remaining, _BRACKET_TYPES, min_confidence, inference_method),
        keep_hidden=True,
    )
    return (
        cleaned,
        f"Removed {selected_node_id} from canvas.",
        no_update,
        None,
        None,
    )


def _canvas_callback_outputs(*, allow_duplicate: bool = False) -> list[Output]:
    kwargs = {"allow_duplicate": True} if allow_duplicate else {}
    return [
        Output("graph-cyto", "elements", **kwargs),
        Output("action-status", "children", **kwargs),
        Output("graph-cyto", "stylesheet", **kwargs),
        Output("selected-node-id", "data", allow_duplicate=True),
        Output("selected-edge-id", "data", allow_duplicate=True),
    ]
