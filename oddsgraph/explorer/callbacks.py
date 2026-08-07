"""Dash callbacks for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import Dash, Input, Output, State, callback_context, html, no_update
from dash.exceptions import PreventUpdate

from oddsgraph.config import Settings
from oddsgraph.explorer.canvas_actions import (
    CanvasMutation,
    _canvas_callback_outputs,
    apply_time_slider,
    filter_canvas,
    highlight_on_tap,
    load_view,
    remove_from_canvas,
)
from oddsgraph.explorer.data import get_edge, get_node
from oddsgraph.explorer.inspector import _hover_card_children, _inspector_sheet
from oddsgraph.explorer.presentation import (
    bracket_summary_text,
    phase_at_hour,
)
from oddsgraph.explorer.shell import (
    build_tracker_items,
    phase_badge_children,
    playback_time_children,
)

_HOUR_SECONDS = 3600

# Re-export public canvas helpers so existing test imports keep working.
__all__ = [
    "apply_time_slider",
    "highlight_on_tap",
    "load_view",
    "next_play_advance",
    "next_play_toggle",
    "phase_view_update",
    "register_callbacks",
    "remove_from_canvas",
]


def next_play_toggle(
    *,
    playing: bool,
    hour_epoch: int | None,
    min_hour: int | None,
    max_hour: int | None,
    slider_disabled: bool,
) -> tuple[bool, str, bool, int | None, str, str]:
    """Compute Play/Pause toggle result.

    Returns
    ``(interval_disabled, button_label, playing, restart_hour, aria_label, aria_pressed)``.
    ``restart_hour`` is set when playback should jump back to tournament start;
    otherwise it is ``None`` (leave the slider unchanged).
    """
    if slider_disabled:
        return True, "Play", False, None, "Play tournament timeline", "false"
    if playing:
        return True, "Play", False, None, "Play tournament timeline", "false"
    if (
        hour_epoch is not None
        and max_hour is not None
        and min_hour is not None
        and int(hour_epoch) >= int(max_hour)
    ):
        return False, "Pause", True, int(min_hour), "Pause tournament timeline", "true"
    return False, "Pause", True, None, "Pause tournament timeline", "true"


def next_play_advance(
    *,
    playing: bool,
    hour_epoch: int | None,
    max_hour: int | None,
) -> tuple[int, bool, str, bool, str, str] | None:
    """Advance one hour while playing, or ``None`` when idle / incomplete.

    Returns
    ``(next_hour, interval_disabled, button_label, playing, aria_label, aria_pressed)``.
    Hitting the end pauses playback at ``max_hour``.
    """
    if not playing:
        return None
    if hour_epoch is None or max_hour is None:
        return None
    next_hour = int(hour_epoch) + _HOUR_SECONDS
    end = int(max_hour)
    if next_hour >= end:
        return end, True, "Play", False, "Play tournament timeline", "false"
    return next_hour, False, "Pause", True, "Pause tournament timeline", "true"


def phase_view_update(
    *,
    hour_epoch: int | None,
    previous_phase_key: str | None,
) -> tuple[Any, Any, Any, str, str]:
    """Build time/phase UI updates; skip tracker rebuild when phase is unchanged.

    Returns ``(time_children, phase_badge, tracker_or_no_update, phase_key, summary)``.

    Summary is phase + selected time only (no canvas match counts). Counts would
    race the scrub callback, which also writes ``graph-cyto.elements``.
    """
    phase = phase_at_hour(hour_epoch)
    time_children = playback_time_children(hour_epoch)
    badge = phase_badge_children(hour_epoch)
    summary = bracket_summary_text(None, hour_epoch)
    if previous_phase_key == phase.key:
        return time_children, badge, no_update, phase.key, summary
    tracker = build_tracker_items(hour_epoch)
    return time_children, badge, tracker, phase.key, summary


def _drawer_classes(base: str, is_open: bool) -> str:
    return f"{base} is-open" if is_open else base


def register_callbacks(app: Dash, settings: Settings) -> None:
    """Wire explorer interactions against ``settings`` artifacts."""

    @app.callback(
        Output("controls-panel", "className"),
        Output("controls-open", "data"),
        Output("toggle-controls", "aria-expanded"),
        Output("inspector-rail", "className", allow_duplicate=True),
        Output("inspector-open", "data", allow_duplicate=True),
        Output("toggle-inspector", "aria-expanded", allow_duplicate=True),
        Output("explorer-root", "className"),
        Input("toggle-controls", "n_clicks"),
        Input("close-controls", "n_clicks"),
        Input("drawer-scrim", "n_clicks"),
        State("controls-open", "data"),
        State("inspector-open", "data"),
        prevent_initial_call=True,
    )
    def toggle_controls(
        toggle_clicks: int | None,
        close_clicks: int | None,
        scrim_clicks: int | None,
        is_open: bool | None,
        inspector_open: bool | None,
    ) -> tuple[str, bool, str, Any, Any, Any, str]:
        del toggle_clicks, close_clicks, scrim_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        if triggered.startswith("drawer-scrim"):
            return (
                "explorer-drawer explorer-controls",
                False,
                "false",
                "explorer-drawer explorer-inspector",
                False,
                "false",
                "explorer-root",
            )
        if triggered.startswith("close-controls"):
            next_open = False
        else:
            next_open = not bool(is_open)
        root = (
            "explorer-root has-drawer-open"
            if (next_open or inspector_open)
            else "explorer-root"
        )
        return (
            _drawer_classes("explorer-drawer explorer-controls", next_open),
            next_open,
            "true" if next_open else "false",
            no_update,
            no_update,
            no_update,
            root,
        )

    @app.callback(
        Output("inspector-rail", "className"),
        Output("inspector-open", "data"),
        Output("toggle-inspector", "aria-expanded"),
        Output("explorer-root", "className", allow_duplicate=True),
        Input("toggle-inspector", "n_clicks"),
        Input("close-inspector", "n_clicks"),
        State("inspector-open", "data"),
        State("controls-open", "data"),
        prevent_initial_call=True,
    )
    def toggle_inspector(
        toggle_clicks: int | None,
        close_clicks: int | None,
        is_open: bool | None,
        controls_open: bool | None,
    ) -> tuple[str, bool, str, str]:
        del toggle_clicks, close_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        if triggered.startswith("close-inspector"):
            next_open = False
        else:
            next_open = not bool(is_open)
        root = (
            "explorer-root has-drawer-open"
            if (next_open or controls_open)
            else "explorer-root"
        )
        return (
            _drawer_classes("explorer-drawer explorer-inspector", next_open),
            next_open,
            "true" if next_open else "false",
            root,
        )

    @app.callback(
        Output("hover-card", "children"),
        Output("hover-card", "style"),
        Input("graph-cyto", "mouseoverNodeData"),
        Input("graph-cyto", "mouseoutNodeData"),
        prevent_initial_call=True,
    )
    def update_hover(
        mouseover: dict[str, Any] | None,
        mouseout: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, str]]:
        del mouseout
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        if triggered.startswith("graph-cyto.mouseoutNodeData"):
            return [], {"display": "none"}
        return _hover_card_children(mouseover)

    @app.callback(
        Output("time-slider-label", "children"),
        Output("phase-badge", "children"),
        Output("stage-tracker", "children"),
        Output("phase-key", "data"),
        Output("bracket-summary", "children"),
        Input("time-slider", "value"),
        State("phase-key", "data"),
        prevent_initial_call=True,
    )
    def update_time_and_phase(
        hour_epoch: int | None,
        previous_phase_key: str | None,
    ) -> tuple[Any, Any, Any, str, str]:
        return phase_view_update(
            hour_epoch=hour_epoch,
            previous_phase_key=previous_phase_key,
        )

    @app.callback(
        Output("confidence-value", "children"),
        Input("confidence-filter", "value"),
    )
    def update_confidence_value(value: float | None) -> str:
        return f"{float(value or 0.0):.2f}"

    @app.callback(
        Output("confidence-filter", "value"),
        Output("inference-filter", "value"),
        Input("reset-filters-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_filters(n_clicks: int | None) -> tuple[float, str]:
        del n_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        return 0.0, ""

    @app.callback(
        Output("time-play-interval", "disabled"),
        Output("time-play-button", "children"),
        Output("time-play-state", "data"),
        Output("time-slider", "value", allow_duplicate=True),
        Output("time-play-button", "aria-label"),
        Output("time-play-button", "aria-pressed"),
        Input("time-play-button", "n_clicks"),
        State("time-play-state", "data"),
        State("time-slider", "value"),
        State("time-slider", "min"),
        State("time-slider", "max"),
        State("time-slider", "disabled"),
        prevent_initial_call=True,
    )
    def toggle_time_play(
        n_clicks: int | None,
        playing: bool | None,
        hour_epoch: int | None,
        min_hour: int | None,
        max_hour: int | None,
        slider_disabled: bool | None,
    ) -> tuple[bool, str, bool, Any, str, str]:
        del n_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        interval_disabled, label, now_playing, restart, aria_label, aria_pressed = (
            next_play_toggle(
                playing=bool(playing),
                hour_epoch=hour_epoch,
                min_hour=min_hour,
                max_hour=max_hour,
                slider_disabled=bool(slider_disabled),
            )
        )
        return (
            interval_disabled,
            label,
            now_playing,
            no_update if restart is None else restart,
            aria_label,
            aria_pressed,
        )

    @app.callback(
        Output("time-slider", "value", allow_duplicate=True),
        Output("time-play-interval", "disabled", allow_duplicate=True),
        Output("time-play-button", "children", allow_duplicate=True),
        Output("time-play-state", "data", allow_duplicate=True),
        Output("time-play-button", "aria-label", allow_duplicate=True),
        Output("time-play-button", "aria-pressed", allow_duplicate=True),
        Input("time-play-interval", "n_intervals"),
        State("time-play-state", "data"),
        State("time-slider", "value"),
        State("time-slider", "max"),
        prevent_initial_call=True,
    )
    def advance_time_play(
        n_intervals: int | None,
        playing: bool | None,
        hour_epoch: int | None,
        max_hour: int | None,
    ) -> tuple[Any, Any, Any, Any, Any, Any]:
        del n_intervals
        result = next_play_advance(
            playing=bool(playing),
            hour_epoch=hour_epoch,
            max_hour=max_hour,
        )
        if result is None:
            raise PreventUpdate
        next_hour, interval_disabled, label, now_playing, aria_label, aria_pressed = result
        if now_playing:
            return next_hour, no_update, no_update, no_update, no_update, no_update
        return next_hour, interval_disabled, label, now_playing, aria_label, aria_pressed

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=False),
        Input("reset-button", "n_clicks"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        State("time-slider", "value"),
        prevent_initial_call=True,
    )
    def reset_view(
        reset_clicks: int | None,
        min_confidence: float | None,
        inference_method: str | None,
        hour_epoch: int | None,
    ) -> CanvasMutation:
        del reset_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        return load_view(
            settings,
            float(min_confidence or 0.0),
            inference_method or "",
            hour_epoch=hour_epoch,
            reset=True,
        )

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("confidence-filter", "value"),
        Input("inference-filter", "value"),
        Input("graph-cyto", "tapNodeData"),
        State("graph-cyto", "elements"),
        prevent_initial_call=True,
    )
    def highlight_and_filter(
        min_confidence: float | None,
        inference_method: str | None,
        tap_node: dict[str, Any] | None,
        elements: list[dict[str, Any]] | None,
    ) -> CanvasMutation:
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        conf = float(min_confidence or 0.0)
        method = inference_method or ""

        if triggered.startswith("graph-cyto.tapNodeData"):
            result = highlight_on_tap(elements, tap_node)
            if result is None:
                raise PreventUpdate
            return result

        if triggered.startswith("confidence-filter") or triggered.startswith(
            "inference-filter"
        ):
            return filter_canvas(elements, conf, method)

        raise PreventUpdate

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("time-slider", "value"),
        State("graph-cyto", "elements"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        prevent_initial_call=True,
    )
    def scrub_time(
        hour_epoch: int | None,
        elements: list[dict[str, Any]] | None,
        min_confidence: float | None,
        inference_method: str | None,
    ) -> CanvasMutation:
        if not callback_context.triggered:
            raise PreventUpdate
        return apply_time_slider(
            elements,
            hour_epoch,
            float(min_confidence or 0.0),
            inference_method or "",
            settings=settings,
        )

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("remove-button", "n_clicks"),
        State("graph-cyto", "elements"),
        State("selected-node-id", "data"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        prevent_initial_call=True,
    )
    def remove_selected(
        remove_clicks: int | None,
        elements: list[dict[str, Any]] | None,
        selected_node_id: str | None,
        min_confidence: float | None,
        inference_method: str | None,
    ) -> CanvasMutation:
        del remove_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        return remove_from_canvas(
            selected_node_id,
            elements,
            float(min_confidence or 0.0),
            inference_method or "",
        )

    @app.callback(
        Output("inspector-panel", "children"),
        Output("selected-node-id", "data"),
        Output("selected-edge-id", "data"),
        Output("remove-button", "disabled"),
        Output("inspector-rail", "className", allow_duplicate=True),
        Output("inspector-open", "data", allow_duplicate=True),
        Output("toggle-inspector", "aria-expanded", allow_duplicate=True),
        Output("explorer-root", "className", allow_duplicate=True),
        Input("graph-cyto", "tapNodeData"),
        Input("graph-cyto", "tapEdgeData"),
        State("controls-open", "data"),
        prevent_initial_call=True,
    )
    def inspect_selection(
        node_data: dict[str, Any] | None,
        edge_data: dict[str, Any] | None,
        controls_open: bool | None,
    ) -> tuple[Any, Any, Any, bool, str, bool, str, str]:
        del controls_open
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        open_classes = "explorer-drawer explorer-inspector is-open"
        root = "explorer-root has-drawer-open"

        if triggered.startswith("graph-cyto.tapNodeData") and node_data:
            node_id = node_data.get("id")
            row = get_node(settings, node_id) if node_id else None
            stage = node_data.get("stage")
            if row is None:
                return (
                    html.P(f"Node not found: {node_id}", className="panel-hint"),
                    node_id,
                    None,
                    False,
                    open_classes,
                    True,
                    "true",
                    root,
                )
            return (
                _inspector_sheet("Node", row, stage=stage, presentation=node_data),
                node_id,
                None,
                False,
                open_classes,
                True,
                "true",
                root,
            )

        if triggered.startswith("graph-cyto.tapEdgeData") and edge_data:
            source = edge_data.get("source")
            target = edge_data.get("target")
            edge_type = edge_data.get("edge_type") or edge_data.get("label")
            edge_id = edge_data.get("id")
            row = (
                get_edge(settings, source, target, edge_type)
                if source and target and edge_type
                else None
            )
            if row is None:
                return (
                    html.P(f"Edge not found: {edge_id}", className="panel-hint"),
                    None,
                    edge_id,
                    True,
                    open_classes,
                    True,
                    "true",
                    root,
                )
            return (
                _inspector_sheet("Edge", row, presentation=edge_data),
                None,
                edge_id,
                True,
                open_classes,
                True,
                "true",
                root,
            )

        raise PreventUpdate
