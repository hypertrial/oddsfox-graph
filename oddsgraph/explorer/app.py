"""Dash application layout for the local graph explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dash import Dash, dcc, html
import dash_cytoscape as cyto

from oddsgraph.config import Settings
from oddsgraph.explorer import VIEW_BRACKET, VIEW_TOPOLOGY
from oddsgraph.explorer.data import bracket_elements, graph_counts
from oddsgraph.explorer.presentation import (
    EDGE_COLORS,
    NODE_COLORS,
    bracket_layout,
    bracket_stylesheet,
    topology_stylesheet,
)
from oddsgraph.ontology import NodeType

# Extra layouts used by the explorer (dagre lives in the extra bundle).
cyto.load_extra_layouts()

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

ALL_NODE_TYPES = [t.value for t in NodeType]

STAGE_STRIP = [
    "Round of 32",
    "Round of 16",
    "Quarterfinals",
    "Semifinals",
    "Final / 3rd",
]


def default_stylesheet(view_mode: str = VIEW_BRACKET) -> list[dict[str, Any]]:
    """Return the Cytoscape stylesheet for the active view."""
    if view_mode == VIEW_TOPOLOGY:
        return topology_stylesheet(NODE_COLORS, EDGE_COLORS)
    return bracket_stylesheet()


def _counts_summary(counts: dict[str, Any]) -> str:
    return (
        f"{counts['total_nodes']:,} nodes · {counts['total_edges']:,} edges "
        f"(source: {counts['source']})"
    )


def build_app(settings: Settings) -> Dash:
    """Build the Dash explorer app bound to ``settings`` build artifacts."""
    from oddsgraph.explorer.callbacks import register_callbacks

    initial = bracket_elements(settings)
    counts = graph_counts(settings)

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
                                        "View",
                                        htmlFor="view-mode",
                                        className="panel-label",
                                    ),
                                    dcc.RadioItems(
                                        id="view-mode",
                                        options=[
                                            {
                                                "label": "Knockout bracket",
                                                "value": VIEW_BRACKET,
                                            },
                                            {
                                                "label": "Full topology",
                                                "value": VIEW_TOPOLOGY,
                                            },
                                        ],
                                        value=VIEW_BRACKET,
                                        className="view-mode-radio",
                                    ),
                                    html.P(
                                        "Left-to-right knockout bracket: 32 MATCH "
                                        "cards, ADVANCES_TO edges, preset layout. "
                                        "Search or expand switches to Full topology.",
                                        className="panel-hint",
                                    ),
                                ],
                            ),
                            html.Hr(className="panel-divider"),
                            html.Div(
                                className="panel-section",
                                children=[
                                    html.Label(
                                        "Search nodes",
                                        htmlFor="search-input",
                                        className="panel-label",
                                    ),
                                    dcc.Input(
                                        id="search-input",
                                        type="text",
                                        placeholder="label, id, or alias…",
                                        debounce=True,
                                        className="search-input",
                                    ),
                                    html.Button(
                                        "Search",
                                        id="search-button",
                                        n_clicks=0,
                                        className="btn btn-primary",
                                        type="button",
                                    ),
                                    html.Div(
                                        id="search-results",
                                        className="search-results",
                                        role="list",
                                    ),
                                ],
                            ),
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
                                    html.Summary("Advanced filters & layout"),
                                    html.Div(
                                        className="advanced-body",
                                        children=[
                                            html.Label(
                                                "Node types on canvas",
                                                htmlFor="type-filter",
                                                className="panel-label",
                                            ),
                                            dcc.Checklist(
                                                id="type-filter",
                                                options=[
                                                    {"label": t, "value": t}
                                                    for t in ALL_NODE_TYPES
                                                ],
                                                value=["MATCH"],
                                                className="type-filter",
                                            ),
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
                                            html.Label(
                                                "Layout",
                                                htmlFor="layout-dropdown",
                                                className="panel-label",
                                            ),
                                            dcc.Dropdown(
                                                id="layout-dropdown",
                                                options=[
                                                    {
                                                        "label": "preset (bracket)",
                                                        "value": "preset",
                                                    },
                                                    {
                                                        "label": "dagre",
                                                        "value": "dagre",
                                                    },
                                                    {
                                                        "label": "breadthfirst",
                                                        "value": "breadthfirst",
                                                    },
                                                    {
                                                        "label": "cose (force)",
                                                        "value": "cose",
                                                    },
                                                    {
                                                        "label": "circle",
                                                        "value": "circle",
                                                    },
                                                    {
                                                        "label": "grid",
                                                        "value": "grid",
                                                    },
                                                    {
                                                        "label": "concentric",
                                                        "value": "concentric",
                                                    },
                                                ],
                                                value="preset",
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
                                elements=initial.to_elements(),
                                stylesheet=default_stylesheet(VIEW_BRACKET),
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
                        className="explorer-inspector is-open",
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
                                        "Expand neighbors",
                                        id="expand-button",
                                        n_clicks=0,
                                        disabled=True,
                                        type="button",
                                    ),
                                    html.Button(
                                        "Remove from canvas",
                                        id="remove-button",
                                        n_clicks=0,
                                        disabled=True,
                                        type="button",
                                    ),
                                    html.Button(
                                        "View in topology",
                                        id="view-in-topology-button",
                                        n_clicks=0,
                                        disabled=True,
                                        type="button",
                                        title=(
                                            "Open this node in the Full topology "
                                            "view with its neighbors"
                                        ),
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
            dcc.Store(id="inspector-open", data=True),
            dcc.Store(id="skip-view-reload", data=False),
        ],
    )

    register_callbacks(app, settings)
    return app
