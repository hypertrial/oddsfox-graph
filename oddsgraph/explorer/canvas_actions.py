"""Pure canvas mutation helpers for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import Output, no_update

from oddsgraph.config import Settings
from oddsgraph.explorer.data import bracket_elements, stage_odds_by_team
from oddsgraph.explorer.filters import apply_filters
from oddsgraph.explorer.presentation import apply_time_slice
from oddsgraph.explorer.tree_render import elements_to_bracket_children

# children, status
CanvasMutation = tuple[Any, Any]

_BRACKET_TYPES = ["MATCH"]


def _project_and_render(
    settings: Settings,
    min_confidence: float,
    inference_method: str,
    *,
    hour_epoch: int | None = None,
) -> Any:
    slice_ = bracket_elements(settings)
    elements = apply_time_slice(
        slice_.to_elements(),
        hour_epoch,
        stage_odds=stage_odds_by_team(settings),
    )
    filtered = apply_filters(elements, _BRACKET_TYPES, min_confidence, inference_method)
    return elements_to_bracket_children(filtered)


def filter_canvas(
    settings: Settings,
    min_confidence: float,
    inference_method: str,
    *,
    hour_epoch: int | None = None,
) -> CanvasMutation:
    """Re-apply confidence/inference filters and re-render the tree."""
    return (
        _project_and_render(
            settings,
            min_confidence,
            inference_method,
            hour_epoch=hour_epoch,
        ),
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
    children = _project_and_render(
        settings,
        min_confidence,
        inference_method,
        hour_epoch=hour_epoch,
    )
    status = "Reset knockout bracket view." if reset else "Loaded knockout bracket view."
    return (children, status)


def apply_time_slider(
    settings: Settings,
    hour_epoch: int | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Update projected teams and advance probabilities for the selected hour."""
    return (
        _project_and_render(
            settings,
            min_confidence,
            inference_method,
            hour_epoch=hour_epoch,
        ),
        no_update,
    )


def _canvas_callback_outputs(*, allow_duplicate: bool = False) -> list[Output]:
    kwargs = {"allow_duplicate": True} if allow_duplicate else {}
    return [
        Output("bracket-canvas", "children", **kwargs),
        Output("action-status", "children", **kwargs),
    ]
