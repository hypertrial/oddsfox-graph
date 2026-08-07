"""Dash callbacks for the local graph explorer."""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from typing import Any

from dash import ALL, Dash, Input, Output, State, callback_context, no_update
from dash.exceptions import PreventUpdate

from oddsgraph.bracket import schedule_playback_milestones
from oddsgraph.bracket_projection import sparkline_points_for_side
from oddsgraph.config import Settings
from oddsgraph.explorer.canvas_actions import (
    CanvasMutation,
    _canvas_callback_outputs,
    apply_time_slider,
    filter_canvas,
    find_projected_match,
    load_view,
)
from oddsgraph.explorer.data import stage_odds_by_team
from oddsgraph.explorer.match_chart import build_match_chart_figure
from oddsgraph.explorer.presentation import (
    bracket_summary_text,
    phase_at_hour,
)
from oddsgraph.explorer.shell import (
    build_tracker_items,
    phase_badge_children,
    playback_time_children,
)
from oddsgraph.explorer.tree_render import (
    BracketLayout,
    DESKTOP_LAYOUT_MIN_WIDTH_PX,
)

# Re-export public canvas helpers so existing test imports keep working.
__all__ = [
    "apply_time_slider",
    "load_view",
    "match_modal_update",
    "next_play_advance",
    "next_play_toggle",
    "phase_view_update",
    "register_callbacks",
]


def _normalize_layout(layout: str | None) -> BracketLayout:
    if layout in {"desktop", "mobile", "both"}:
        return layout  # type: ignore[return-value]
    return "both"


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
    ``restart_hour`` is set when playback should jump back to Round of 32 start;
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
    milestones: Sequence[int],
) -> tuple[int, bool, str, bool, str, str] | None:
    """Advance one playback milestone while playing, or ``None`` when idle.

    Returns
    ``(next_hour, interval_disabled, button_label, playing, aria_label, aria_pressed)``.
    Hitting the end pauses playback at ``max_hour``.
    """
    if not playing:
        return None
    if hour_epoch is None or max_hour is None or not milestones:
        return None
    end = int(max_hour)
    idx = bisect.bisect_right(milestones, int(hour_epoch))
    next_hour = milestones[idx] if idx < len(milestones) else None
    if next_hour is None or next_hour >= end:
        return end, True, "Play", False, "Play tournament timeline", "false"
    return next_hour, False, "Pause", True, "Pause tournament timeline", "true"


def phase_view_update(
    *,
    hour_epoch: int | None,
    previous_phase_key: str | None,
) -> tuple[Any, Any, Any, str, str]:
    """Build time/phase UI updates; skip tracker rebuild when phase is unchanged.

    Returns ``(time_children, phase_badge, tracker_or_no_update, phase_key, summary)``.
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


def _modal_classes(*, is_open: bool) -> str:
    return "match-modal is-open" if is_open else "match-modal"


def match_modal_update(
    *,
    triggered_id: Any,
    settings: Settings,
    hour_epoch: int | None,
) -> tuple[str, Any, Any, str]:
    """Compute match-modal open/close state and figure.

    Returns ``(modal_class, title, figure, aria_hidden)``.
    """
    if isinstance(triggered_id, str) and triggered_id in {
        "match-modal-scrim",
        "match-modal-close",
    }:
        return _modal_classes(is_open=False), no_update, no_update, "true"

    match_id: str | None = None
    if isinstance(triggered_id, dict) and triggered_id.get("type") == "match-card":
        match_id = str(triggered_id.get("match_id") or "")
    if not match_id:
        raise PreventUpdate

    data = find_projected_match(settings, match_id, hour_epoch)
    if data is None:
        raise PreventUpdate
    home = data.get("home_team")
    away = data.get("away_team")
    if not home and not away:
        raise PreventUpdate

    stage_odds = stage_odds_by_team(settings)
    home_points = sparkline_points_for_side(
        data,
        str(home) if home else None,
        "home",
        hour_epoch,
        stage_odds,
    )
    away_points = sparkline_points_for_side(
        data,
        str(away) if away else None,
        "away",
        hour_epoch,
        stage_odds,
    )
    home_label = str(home) if home else "Home"
    away_label = str(away) if away else "Away"
    title = f"{home_label} vs {away_label} — Full match odds"
    figure = build_match_chart_figure(
        home_points,
        away_points,
        home_label=home_label,
        away_label=away_label,
        match_start_epoch=(
            int(data["match_start_epoch"])
            if data.get("match_start_epoch") is not None
            else None
        ),
        match_end_epoch=(
            int(data["match_end_epoch"])
            if data.get("match_end_epoch") is not None
            else None
        ),
    )
    return _modal_classes(is_open=True), title, figure, "false"


def register_callbacks(app: Dash, settings: Settings) -> None:
    """Wire explorer interactions against ``settings`` artifacts."""

    @app.callback(
        Output("controls-panel", "className"),
        Output("controls-open", "data"),
        Output("toggle-controls", "aria-expanded"),
        Output("explorer-root", "className"),
        Input("toggle-controls", "n_clicks"),
        Input("close-controls", "n_clicks"),
        Input("drawer-scrim", "n_clicks"),
        State("controls-open", "data"),
        prevent_initial_call=True,
    )
    def toggle_controls(
        toggle_clicks: int | None,
        close_clicks: int | None,
        scrim_clicks: int | None,
        is_open: bool | None,
    ) -> tuple[str, bool, str, str]:
        del toggle_clicks, close_clicks, scrim_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        if triggered.startswith("drawer-scrim") or triggered.startswith("close-controls"):
            next_open = False
        else:
            next_open = not bool(is_open)
        root = "explorer-root has-drawer-open" if next_open else "explorer-root"
        return (
            _drawer_classes("explorer-drawer explorer-controls", next_open),
            next_open,
            "true" if next_open else "false",
            root,
        )

    @app.callback(
        Output("match-modal", "className"),
        Output("match-modal-title", "children"),
        Output("match-modal-graph", "figure"),
        Output("match-modal", "aria-hidden"),
        Input({"type": "match-card", "match_id": ALL, "surface": ALL}, "n_clicks"),
        Input("match-modal-scrim", "n_clicks"),
        Input("match-modal-close", "n_clicks"),
        State("time-slider", "value"),
        prevent_initial_call=True,
    )
    def toggle_match_modal(
        card_clicks: list[int | None] | None,
        scrim_clicks: int | None,
        close_clicks: int | None,
        hour_epoch: int | None,
    ) -> tuple[Any, Any, Any, str]:
        del card_clicks, scrim_clicks, close_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        # Ignore spurious ALL-input fires when the canvas re-renders with fresh
        # n_clicks=0 cards (no real user click yet).
        triggered = callback_context.triggered[0]
        if (
            isinstance(callback_context.triggered_id, dict)
            and callback_context.triggered_id.get("type") == "match-card"
            and triggered.get("value") in (None, 0)
        ):
            raise PreventUpdate
        return match_modal_update(
            triggered_id=callback_context.triggered_id,
            settings=settings,
            hour_epoch=hour_epoch,
        )

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
            milestones=schedule_playback_milestones(),
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
        State("viewport-layout", "data"),
        prevent_initial_call=True,
    )
    def reset_view(
        reset_clicks: int | None,
        min_confidence: float | None,
        inference_method: str | None,
        hour_epoch: int | None,
        layout: str | None,
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
            layout=_normalize_layout(layout),
        )

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("confidence-filter", "value"),
        Input("inference-filter", "value"),
        State("time-slider", "value"),
        State("viewport-layout", "data"),
        prevent_initial_call=True,
    )
    def filter_canvas_cb(
        min_confidence: float | None,
        inference_method: str | None,
        hour_epoch: int | None,
        layout: str | None,
    ) -> CanvasMutation:
        if not callback_context.triggered:
            raise PreventUpdate
        return filter_canvas(
            settings,
            float(min_confidence or 0.0),
            inference_method or "",
            hour_epoch=hour_epoch,
            layout=_normalize_layout(layout),
        )

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Output("previous-hour", "data"),
        Input("time-slider", "value"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        State("previous-hour", "data"),
        State("viewport-layout", "data"),
        prevent_initial_call=True,
    )
    def scrub_time(
        hour_epoch: int | None,
        min_confidence: float | None,
        inference_method: str | None,
        previous_hour: int | None,
        layout: str | None,
    ) -> tuple[Any, Any, Any]:
        if not callback_context.triggered:
            raise PreventUpdate
        children, status = apply_time_slider(
            settings,
            hour_epoch,
            float(min_confidence or 0.0),
            inference_method or "",
            previous_hour_epoch=previous_hour,
            layout=_normalize_layout(layout),
        )
        return children, status, hour_epoch

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("viewport-layout", "data"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        State("time-slider", "value"),
        prevent_initial_call=True,
    )
    def relayout_canvas(
        layout: str | None,
        min_confidence: float | None,
        inference_method: str | None,
        hour_epoch: int | None,
    ) -> CanvasMutation:
        if not callback_context.triggered:
            raise PreventUpdate
        return filter_canvas(
            settings,
            float(min_confidence or 0.0),
            inference_method or "",
            hour_epoch=hour_epoch,
            layout=_normalize_layout(layout),
        )

    app.clientside_callback(
        f"""
        function(_n, current) {{
            const width = window.innerWidth || 0;
            const next = width >= {DESKTOP_LAYOUT_MIN_WIDTH_PX} ? "desktop" : "mobile";
            if (next === current) {{
                return window.dash_clientside.no_update;
            }}
            return next;
        }}
        """,
        Output("viewport-layout", "data"),
        Input("viewport-layout-probe", "n_intervals"),
        State("viewport-layout", "data"),
    )
