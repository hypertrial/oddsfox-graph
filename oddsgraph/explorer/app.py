"""Dash application layout for the local graph explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dash import Dash, dcc, html

from oddsgraph.config import Settings
from oddsgraph.explorer.canvas_actions import load_view
from oddsgraph.explorer.data import (
    graph_counts,
    odds_time_bounds,
)
from oddsgraph.explorer.presentation import (
    bracket_summary_text,
    phase_at_hour,
    time_slider_marks,
)
from oddsgraph.explorer.shell import (
    build_tracker,
    phase_badge_children,
    playback_time_children,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Playback advances one kickoff or full-time milestone at a time.
TIME_PLAY_MS_PER_STEP = 400


def _counts_summary(counts: dict[str, Any]) -> str:
    return (
        f"{counts['total_nodes']:,} nodes · {counts['total_edges']:,} edges "
        f"(source: {counts['source']})"
    )


def _compact_counts(counts: dict[str, Any]) -> str:
    return f"{counts['total_nodes']:,} · {counts['total_edges']:,}"


def build_app(settings: Settings) -> Dash:
    """Build the Dash explorer app bound to ``settings`` build artifacts."""
    from oddsgraph.explorer.callbacks import register_callbacks

    counts = graph_counts(settings)
    min_hour, max_hour = odds_time_bounds(settings)
    if min_hour is None or max_hour is None:
        min_hour, max_hour = 0, 1
        slider_disabled = True
        slider_value = 0
        marks = {0: "n/a", 1: "n/a"}
    else:
        slider_disabled = False
        slider_value = min_hour
        marks = time_slider_marks(min_hour, max_hour)

    initial_children, _status = load_view(
        settings,
        0.0,
        "",
        hour_epoch=None if slider_disabled else slider_value,
    )
    initial_phase = phase_at_hour(None if slider_disabled else slider_value)
    counts_full = _counts_summary(counts)

    app = Dash(
        __name__,
        title="OddsFox Graph explorer",
        assets_folder=str(ASSETS_DIR),
        suppress_callback_exceptions=True,
        update_title=None,
    )
    app.layout = html.Div(
        id="explorer-root",
        className="explorer-root",
        children=[
            html.Header(
                className="explorer-header",
                role="banner",
                children=[
                    html.Div(
                        className="explorer-brand",
                        children=[
                            html.Img(
                                src="/assets/oddsfox-favicon.png",
                                alt="",
                                className="explorer-logo",
                                width=28,
                                height=28,
                            ),
                            html.Div(
                                className="explorer-brand-text",
                                children=[
                                    html.H1("OddsFox Graph", className="explorer-title"),
                                    html.P(
                                        "World Cup 2026 · Knockout explorer",
                                        className="explorer-subtitle",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="explorer-header-actions",
                        children=[
                            html.Span(
                                id="header-counts",
                                className="header-counts",
                                children=_compact_counts(counts),
                                title=counts_full,
                            ),
                            html.Button(
                                "Filters & legend",
                                id="toggle-controls",
                                n_clicks=0,
                                className="btn btn-ghost",
                                type="button",
                                **{
                                    "aria-expanded": "false",
                                    "aria-controls": "controls-panel",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            html.Nav(
                className="stage-tracker-wrap",
                children=[build_tracker(initial_phase.key, None if slider_disabled else slider_value)],
                **{"aria-label": "Tournament phase tracker"},
            ),
            html.Div(
                className="explorer-body",
                children=[
                    html.Div(
                        id="drawer-scrim",
                        className="drawer-scrim",
                        n_clicks=0,
                        role="presentation",
                    ),
                    html.Aside(
                        id="controls-panel",
                        className="explorer-drawer explorer-controls",
                        children=[
                            html.Div(
                                className="drawer-header",
                                children=[
                                    html.H2("Filters & legend", className="drawer-title"),
                                    html.Button(
                                        "Close",
                                        id="close-controls",
                                        n_clicks=0,
                                        className="btn btn-ghost btn-icon",
                                        type="button",
                                    ),
                                ],
                            ),
                            html.Section(
                                className="drawer-section",
                                children=[
                                    html.H3("Filters", className="drawer-section-title"),
                                    html.Label(
                                        "Min confidence",
                                        htmlFor="confidence-filter",
                                        className="panel-label",
                                    ),
                                    dcc.Slider(
                                        id="confidence-filter",
                                        min=0,
                                        max=1,
                                        step=0.05,
                                        value=0,
                                        marks={0: "0", 0.5: "0.5", 1: "1"},
                                        allow_direct_input=False,
                                        tooltip=False,
                                    ),
                                    html.P(
                                        id="confidence-value",
                                        className="filter-value",
                                        children="0.00",
                                    ),
                                    html.Label(
                                        "Inference method",
                                        htmlFor="inference-filter",
                                        className="panel-label",
                                    ),
                                    dcc.Dropdown(
                                        id="inference-filter",
                                        options=[
                                            {"label": "(any)", "value": ""},
                                            {
                                                "label": "deterministic",
                                                "value": "deterministic",
                                            },
                                            {
                                                "label": "official_bracket",
                                                "value": "official_bracket",
                                            },
                                            {"label": "llm", "value": "llm"},
                                            {
                                                "label": "unknown",
                                                "value": "unknown",
                                            },
                                        ],
                                        value="",
                                        clearable=False,
                                    ),
                                    html.Button(
                                        "Reset filters",
                                        id="reset-filters-button",
                                        n_clicks=0,
                                        className="btn",
                                        type="button",
                                    ),
                                ],
                            ),
                            html.Section(
                                className="drawer-section",
                                children=[
                                    html.H3("Legend", className="drawer-section-title"),
                                    html.Ul(
                                        className="drawer-legend-pills",
                                        children=[
                                            html.Li(
                                                className="legend-pill is-correct",
                                                children=[
                                                    html.Span(
                                                        className="legend-dot",
                                                        **{"aria-hidden": "true"},
                                                    ),
                                                    "Resolved",
                                                ],
                                            ),
                                            html.Li(
                                                className="legend-pill is-pending",
                                                children=[
                                                    html.Span(
                                                        className="legend-dot",
                                                        **{"aria-hidden": "true"},
                                                    ),
                                                    "Pending",
                                                ],
                                            ),
                                            html.Li(
                                                className="legend-pill is-diverged",
                                                children=[
                                                    html.Span(
                                                        className="legend-dot",
                                                        **{"aria-hidden": "true"},
                                                    ),
                                                    "Diverged",
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            html.Section(
                                className="drawer-section",
                                children=[
                                    html.H3("Projections", className="drawer-section-title"),
                                    html.P(
                                        "Scrub tournament time to reproject unresolved knockout matchups.",
                                        className="panel-hint",
                                    ),
                                    html.Details(
                                        className="help-details",
                                        open=False,
                                        children=[
                                            html.Summary("How projections work"),
                                            html.P(
                                                "Play advances one kickoff or full-time at a time "
                                                "(simultaneous fixtures share a step). "
                                                "Each card shows projected participants and the probability "
                                                "each advances from that round (normalized from stage markets). "
                                                "Resolved games lock to 100% / 0%, including "
                                                "Final and Third Place once those results lock at full time.",
                                                className="panel-hint",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            html.Section(
                                className="drawer-section",
                                children=[
                                    html.H3("Data source", className="drawer-section-title"),
                                    html.P(
                                        f"build: {settings.build_dir}",
                                        className="data-source-path",
                                        title=str(settings.build_dir),
                                    ),
                                    html.P(counts_full, className="panel-hint"),
                                ],
                            ),
                        ],
                    ),
                    html.Main(
                        className="explorer-canvas-wrap",
                        children=[
                            html.P(
                                id="bracket-summary",
                                className="visually-hidden",
                                children=bracket_summary_text(
                                    None,
                                    None if slider_disabled else slider_value,
                                ),
                            ),
                            html.Div(
                                id="bracket-canvas",
                                children=initial_children,
                            ),
                        ],
                    ),
                    html.Div(
                        className="playback-dock",
                        children=[
                            html.Div(
                                className="playback-dock-main",
                                children=[
                                    html.Button(
                                        "Play",
                                        id="time-play-button",
                                        n_clicks=0,
                                        className="btn btn-play",
                                        type="button",
                                        disabled=slider_disabled,
                                        title=(
                                            "Advance one kickoff or full-time "
                                            "at a time"
                                        ),
                                        **{
                                            "aria-label": "Play tournament timeline",
                                            "aria-pressed": "false",
                                        },
                                    ),
                                    html.Div(
                                        className="playback-meta",
                                        children=[
                                            html.Div(
                                                id="time-slider-label",
                                                className="playback-time",
                                                children=playback_time_children(
                                                    None if slider_disabled else slider_value
                                                ),
                                            ),
                                            html.Div(
                                                id="phase-badge",
                                                className="phase-badge",
                                                children=phase_badge_children(
                                                    None if slider_disabled else slider_value
                                                ),
                                            ),
                                        ],
                                    ),
                                    html.Button(
                                        "Reset view",
                                        id="reset-button",
                                        n_clicks=0,
                                        className="btn btn-secondary",
                                        type="button",
                                        title="Restore the full knockout bracket",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="playback-slider-wrap",
                                children=[
                                    html.Label(
                                        "Tournament time",
                                        htmlFor="time-slider",
                                        className="visually-hidden",
                                    ),
                                    dcc.Slider(
                                        id="time-slider",
                                        min=min_hour,
                                        max=max_hour,
                                        step=None,
                                        value=slider_value,
                                        marks=marks,
                                        disabled=slider_disabled,
                                        allow_direct_input=False,
                                        tooltip=False,
                                    ),
                                    html.P(
                                        (
                                            "Schedule bounds unavailable."
                                            if slider_disabled
                                            else ""
                                        ),
                                        id="playback-status",
                                        className="playback-status",
                                    ),
                                    html.Div(
                                        id="action-status",
                                        className="action-status",
                                        role="status",
                                        **{"aria-live": "polite"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            dcc.Store(id="controls-open", data=False),
            dcc.Store(id="time-play-state", data=False),
            dcc.Store(id="phase-key", data=initial_phase.key),
            dcc.Interval(
                id="time-play-interval",
                interval=TIME_PLAY_MS_PER_STEP,
                n_intervals=0,
                disabled=True,
            ),
        ],
    )

    register_callbacks(app, settings)
    return app
