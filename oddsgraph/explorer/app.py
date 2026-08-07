"""Dash application layout for the local graph explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dash import Dash, dcc, html
import dash_cytoscape as cyto

from oddsgraph.config import Settings
from oddsgraph.explorer.data import (
    bracket_elements,
    graph_counts,
    odds_time_bounds,
    stage_odds_by_team,
)
from oddsgraph.explorer.presentation import (
    BRACKET_COLUMN_HEADERS,
    apply_time_slice,
    bracket_layout,
    bracket_stylesheet,
    format_hour_label,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

STAGE_STRIP = [label for _, label in BRACKET_COLUMN_HEADERS]

# Playback advances one hour at a time; one tournament day takes 2 real seconds.
TIME_PLAY_MS_PER_HOUR = max(1, round(2000 / 24))


def _counts_summary(counts: dict[str, Any]) -> str:
    return (
        f"{counts['total_nodes']:,} nodes · {counts['total_edges']:,} edges "
        f"(source: {counts['source']})"
    )


def _time_slider_marks(min_hour: int, max_hour: int) -> dict[int, str]:
    mid = min_hour + ((max_hour - min_hour) // 2)
    return {
        min_hour: format_hour_label(min_hour).replace(" UTC", "").strip(),
        mid: format_hour_label(mid).replace(" UTC", "").strip(),
        max_hour: format_hour_label(max_hour).replace(" UTC", "").strip(),
    }


def build_app(settings: Settings) -> Dash:
    """Build the Dash explorer app bound to ``settings`` build artifacts."""
    from oddsgraph.explorer.callbacks import register_callbacks

    initial = bracket_elements(settings)
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
        marks = _time_slider_marks(min_hour, max_hour)

    initial_elements = apply_time_slice(
        initial.to_elements(),
        slider_value,
        stage_odds=stage_odds_by_team(settings),
    )

    app = Dash(
        __name__,
        title="oddsgraph explorer",
        assets_folder=str(ASSETS_DIR),
        suppress_callback_exceptions=True,
    )
    app.layout = html.Div(
        className="explorer-root",
        children=[
            html.Header(
                className="explorer-header",
                children=[
                    html.Div(
                        className="explorer-header-main",
                        children=[
                            html.H1("oddsgraph explorer", className="explorer-title"),
                            html.Div(
                                className="explorer-meta",
                                children=[
                                    html.Span(f"build: {settings.build_dir}"),
                                    html.Span(" · "),
                                    html.Span(
                                        id="header-counts",
                                        children=_counts_summary(counts),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="explorer-stage-strip",
                        role="list",
                        children=[
                            html.Span(
                                label,
                                className=(
                                    "stage-chip is-terminal"
                                    if "Final" in label
                                    else "stage-chip"
                                ),
                                role="listitem",
                            )
                            for label in STAGE_STRIP
                        ],
                    ),
                ],
            ),
            html.Div(
                className="explorer-body",
                children=[
                    html.Button(
                        "Controls",
                        id="toggle-controls",
                        n_clicks=0,
                        className="rail-toggle rail-toggle-controls",
                        type="button",
                    ),
                    html.Button(
                        "Inspector",
                        id="toggle-inspector",
                        n_clicks=0,
                        className="rail-toggle rail-toggle-inspector",
                        type="button",
                    ),
                    html.Aside(
                        id="controls-panel",
                        className="explorer-controls is-open",
                        children=[
                            html.Div(
                                className="panel-section",
                                children=[
                                    html.Label(
                                        "Tournament time",
                                        htmlFor="time-slider",
                                        className="panel-label",
                                    ),
                                    html.Div(
                                        id="time-slider-label",
                                        className="time-slider-label",
                                        children=format_hour_label(
                                            None if slider_disabled else slider_value
                                        ),
                                    ),
                                    dcc.Slider(
                                        id="time-slider",
                                        min=min_hour,
                                        max=max_hour,
                                        step=3600,
                                        value=slider_value,
                                        marks=marks,
                                        disabled=slider_disabled,
                                        tooltip={
                                            "placement": "bottom",
                                            "always_visible": False,
                                        },
                                    ),
                                    html.Div(
                                        className="time-play-row",
                                        children=[
                                            html.Button(
                                                "Play",
                                                id="time-play-button",
                                                n_clicks=0,
                                                className="btn",
                                                type="button",
                                                disabled=slider_disabled,
                                                title=(
                                                    "Advance one hour at a time "
                                                    "(1 day = 2 seconds)"
                                                ),
                                            ),
                                        ],
                                    ),
                                    html.P(
                                        "Scrub from tournament start to end. Play advances "
                                        "one hour at a time (one day every 2 seconds). "
                                        "Each card shows projected participants and the "
                                        "probability each advances from that round "
                                        "(normalized from stage markets). Green tint = "
                                        "match resolved; dashed borders mark projected "
                                        "future matchups. Resolved games lock to the winner; "
                                        "Final shows Champion and Third Place shows 3rd.",
                                        className="panel-hint",
                                    ),
                                ],
                            ),
                            html.Hr(className="panel-divider"),
                            html.Button(
                                "Reset view",
                                id="reset-button",
                                n_clicks=0,
                                className="btn btn-primary",
                                type="button",
                            ),
                            html.Details(
                                className="advanced-details",
                                open=False,
                                children=[
                                    html.Summary("Advanced filters"),
                                    html.Div(
                                        className="advanced-body",
                                        children=[
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
                                                tooltip={"placement": "bottom"},
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
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Main(
                        className="explorer-canvas-wrap",
                        children=[
                            cyto.Cytoscape(
                                id="graph-cyto",
                                elements=initial_elements,
                                stylesheet=bracket_stylesheet(),
                                layout=bracket_layout(),
                                style={"width": "100%", "height": "100%"},
                                minZoom=0.15,
                                maxZoom=2.5,
                                zoom=1,
                                boxSelectionEnabled=True,
                                responsive=True,
                                userZoomingEnabled=True,
                                userPanningEnabled=True,
                            ),
                            html.Div(
                                id="hover-card",
                                className="hover-card",
                                style={"display": "none"},
                                children=[],
                            ),
                        ],
                    ),
                    html.Aside(
                        id="inspector-rail",
                        className="explorer-inspector",
                        children=[
                            html.H2("Inspector", className="inspector-title"),
                            html.Div(
                                id="inspector-panel",
                                children=html.P(
                                    "Click a match to inspect features and "
                                    "highlight its path to the Final.",
                                    className="panel-hint",
                                ),
                            ),
                            html.Div(
                                className="action-row",
                                children=[
                                    html.Button(
                                        "Remove from canvas",
                                        id="remove-button",
                                        n_clicks=0,
                                        disabled=True,
                                        type="button",
                                    ),
                                ],
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
            dcc.Store(id="selected-node-id", data=None),
            dcc.Store(id="selected-edge-id", data=None),
            dcc.Store(id="controls-open", data=True),
            dcc.Store(id="inspector-open", data=False),
            dcc.Store(id="time-play-state", data=False),
            dcc.Interval(
                id="time-play-interval",
                interval=TIME_PLAY_MS_PER_HOUR,
                n_intervals=0,
                disabled=True,
            ),
        ],
    )

    register_callbacks(app, settings)
    return app
