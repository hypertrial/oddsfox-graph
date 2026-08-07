"""Pure canvas mutation helpers for the local graph explorer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dash import Output, no_update

from oddsgraph.config import Settings
from oddsgraph.explorer.data import bracket_elements, stage_odds_by_team
from oddsgraph.explorer.filters import apply_filters
from oddsgraph.explorer.presentation import apply_time_slice
from oddsgraph.explorer.tree_render import BracketLayout, elements_to_bracket_children

# children, status
CanvasMutation = tuple[Any, Any]

_BRACKET_TYPES = ["MATCH"]

BracketLayoutChoice = BracketLayout


@dataclass
class _ProjectedFrame:
    """Last projected elements for odds-motion without reprojecting."""

    hour_epoch: int | None
    elements: list[dict[str, Any]]
    token: tuple[Any, ...]


@dataclass
class _FrameCache:
    frame: _ProjectedFrame | None = None
    # Tracks projection calls for tests / microbenches.
    project_count: int = 0


_FRAME_CACHE = _FrameCache()


def reset_projected_frame_cache() -> None:
    """Clear the process-local last-frame cache (tests / artifact reload)."""
    _FRAME_CACHE.frame = None
    _FRAME_CACHE.project_count = 0


def projected_frame_cache_stats() -> dict[str, int]:
    return {"project_count": _FRAME_CACHE.project_count}


def _artifact_token(settings: Settings) -> tuple[Any, ...]:
    paths = (
        settings.nodes_path,
        settings.edges_path,
        settings.odds_history_path,
        settings.stage_odds_history_path,
    )
    mtimes: list[float] = []
    for path in paths:
        try:
            mtimes.append(path.stat().st_mtime if path.exists() else -1.0)
        except OSError:
            mtimes.append(-1.0)
    return (str(settings.build_dir), *mtimes)


def _project_elements(
    settings: Settings,
    hour_epoch: int | None,
    *,
    previous_hour_epoch: int | None = None,
    previous_elements: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    slice_ = bracket_elements(settings)
    base = slice_.to_elements()
    stage_odds = stage_odds_by_team(settings)
    token = _artifact_token(settings)

    cached_previous = previous_elements
    if (
        cached_previous is None
        and previous_hour_epoch is not None
        and hour_epoch is not None
        and int(previous_hour_epoch) != int(hour_epoch)
        and _FRAME_CACHE.frame is not None
        and _FRAME_CACHE.frame.token == token
        and _FRAME_CACHE.frame.hour_epoch is not None
        and int(_FRAME_CACHE.frame.hour_epoch) == int(previous_hour_epoch)
    ):
        cached_previous = _FRAME_CACHE.frame.elements

    if (
        cached_previous is None
        and previous_hour_epoch is not None
        and hour_epoch is not None
        and int(previous_hour_epoch) != int(hour_epoch)
    ):
        # Cold cache / first scrub after reload: project previous once.
        _FRAME_CACHE.project_count += 1
        cached_previous = apply_time_slice(
            base,
            previous_hour_epoch,
            stage_odds=stage_odds,
        )

    _FRAME_CACHE.project_count += 1
    projected = apply_time_slice(
        base,
        hour_epoch,
        stage_odds=stage_odds,
        previous_elements=cached_previous,
    )
    _FRAME_CACHE.frame = _ProjectedFrame(
        hour_epoch=hour_epoch,
        elements=projected,
        token=token,
    )
    return projected


def _project_and_render(
    settings: Settings,
    min_confidence: float,
    inference_method: str,
    *,
    hour_epoch: int | None = None,
    previous_hour_epoch: int | None = None,
    layout: BracketLayoutChoice = "both",
    reuse_previous_frame: bool = True,
) -> Any:
    previous_elements = None
    if not reuse_previous_frame:
        reset_projected_frame_cache()
    elements = _project_elements(
        settings,
        hour_epoch,
        previous_hour_epoch=previous_hour_epoch,
        previous_elements=previous_elements,
    )
    filtered = apply_filters(elements, _BRACKET_TYPES, min_confidence, inference_method)
    return elements_to_bracket_children(filtered, layout=layout)


def filter_canvas(
    settings: Settings,
    min_confidence: float,
    inference_method: str,
    *,
    hour_epoch: int | None = None,
    layout: BracketLayoutChoice = "both",
) -> CanvasMutation:
    """Re-apply confidence/inference filters and re-render the tree."""
    return (
        _project_and_render(
            settings,
            min_confidence,
            inference_method,
            hour_epoch=hour_epoch,
            layout=layout,
            reuse_previous_frame=True,
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
    layout: BracketLayoutChoice = "both",
) -> CanvasMutation:
    """Reload the knockout bracket view."""
    children = _project_and_render(
        settings,
        min_confidence,
        inference_method,
        hour_epoch=hour_epoch,
        layout=layout,
        reuse_previous_frame=not reset,
    )
    if reset:
        # Keep the newly projected frame as the motion baseline.
        pass
    status = "Reset knockout bracket view." if reset else "Loaded knockout bracket view."
    return (children, status)


def apply_time_slider(
    settings: Settings,
    hour_epoch: int | None,
    min_confidence: float,
    inference_method: str,
    *,
    previous_hour_epoch: int | None = None,
    layout: BracketLayoutChoice = "both",
) -> CanvasMutation:
    """Update projected teams and advance probabilities for the selected hour."""
    return (
        _project_and_render(
            settings,
            min_confidence,
            inference_method,
            hour_epoch=hour_epoch,
            previous_hour_epoch=previous_hour_epoch,
            layout=layout,
        ),
        no_update,
    )


def _canvas_callback_outputs(*, allow_duplicate: bool = False) -> list[Output]:
    kwargs = {"allow_duplicate": True} if allow_duplicate else {}
    return [
        Output("bracket-canvas", "children", **kwargs),
        Output("action-status", "children", **kwargs),
    ]


__all__ = [
    "CanvasMutation",
    "apply_time_slider",
    "filter_canvas",
    "load_view",
    "projected_frame_cache_stats",
    "reset_projected_frame_cache",
]
