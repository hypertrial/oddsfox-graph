"""Dash application layout for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import Dash, dcc, html
import dash_cytoscape as cyto

from oddsgraph.config import Settings
from oddsgraph.explorer import TOPOLOGY_NODE_TYPES
from oddsgraph.explorer.data import graph_counts, topology_elements
from oddsgraph.ontology import NodeType

# Extra layouts used by the explorer (cose is built-in; breadthfirst too).
cyto.load_extra_layouts()

NODE_COLORS: dict[str, str] = {
    "COMPETITION": "#1f4e79",
    "STAGE": "#2e75b6",
    "GROUP": "#5b9bd5",
    "ROUND": "#9dc3e6",
    "MATCH": "#ed7d31",
    "TEAM": "#70ad47",
    "EVENT": "#7030a0",
    "MARKET": "#ffc000",
    "OUTCOME": "#a5a5a5",
}

EDGE_COLORS: dict[str, str] = {
    "PART_OF": "#5b9bd5",
    "PARTICIPATES_IN": "#70ad47",
    "QUALIFIES_FOR": "#ed7d31",
    "ADVANCES_TO": "#c00000",
    "HAS_MARKET": "#7030a0",
    "HAS_OUTCOME": "#a5a5a5",
    "PRICES": "#ffc000",
    "IMPLIES": "#7f7f7f",
}

ALL_NODE_TYPES = [t.value for t in NodeType]


def default_stylesheet() -> list[dict[str, Any]]:
    styles: list[dict[str, Any]] = [
        {
            "selector": "node",
            "style": {
                "label": "data(label)",
                "text-valign": "center",
                "text-halign": "center",
                "font-size": "10px",
                "color": "#111",
                "background-color": "#888",
                "width": 28,
                "height": 28,
                "text-wrap": "wrap",
                "text-max-width": 80,
            },
        },
        {
            "selector": "edge",
            "style": {
                "label": "data(label)",
                "font-size": "8px",
                "color": "#444",
                "curve-style": "bezier",
                "target-arrow-shape": "triangle",
                "arrow-scale": 0.8,
                "width": 1.5,
                "line-color": "#999",
                "target-arrow-color": "#999",
                "text-rotation": "autorotate",
                "text-margin-y": -8,
            },
        },
        {
            "selector": ":selected",
            "style": {
                "border-width": 3,
                "border-color": "#000",
                "line-color": "#000",
                "target-arrow-color": "#000",
                "z-index": 999,
            },
        },
        {
            "selector": ".hidden",
            "style": {"display": "none"},
        },
    ]
    for node_type, color in NODE_COLORS.items():
        styles.append(
            {
                "selector": f".{node_type}",
                "style": {"background-color": color},
            }
        )
    for edge_type, color in EDGE_COLORS.items():
        styles.append(
            {
                "selector": f".{edge_type}",
                "style": {
                    "line-color": color,
                    "target-arrow-color": color,
                },
            }
        )
    # Emphasize bracket progression.
    styles.append(
        {
            "selector": ".ADVANCES_TO",
            "style": {"width": 2.5},
        }
    )
    return styles


def _counts_summary(counts: dict[str, Any]) -> str:
    return (
        f"{counts['total_nodes']:,} nodes · {counts['total_edges']:,} edges "
        f"(source: {counts['source']})"
    )


def build_app(settings: Settings) -> Dash:
    """Build the Dash explorer app bound to ``settings`` build artifacts."""
    from oddsgraph.explorer.callbacks import register_callbacks

    initial = topology_elements(settings)
    counts = graph_counts(settings)

    app = Dash(__name__, title="oddsgraph explorer")
    app.layout = html.Div(
        className="explorer-root",
        style={
            "fontFamily": "system-ui, -apple-system, Segoe UI, sans-serif",
            "display": "flex",
            "flexDirection": "column",
            "height": "100vh",
            "margin": 0,
        },
        children=[
            html.Div(
                style={
                    "padding": "10px 16px",
                    "borderBottom": "1px solid #ddd",
                    "background": "#f7f7f7",
                },
                children=[
                    html.H2("oddsgraph explorer", style={"margin": "0 0 4px 0"}),
                    html.Div(
                        [
                            html.Span(f"build: {settings.build_dir}"),
                            html.Span(" · "),
                            html.Span(id="header-counts", children=_counts_summary(counts)),
                        ],
                        style={"fontSize": "13px", "color": "#555"},
                    ),
                    html.Div(
                        "Default view is topology only (COMPETITION/STAGE/GROUP/ROUND/"
                        "MATCH/TEAM). EVENT/MARKET/OUTCOME are disconnected from topology "
                        "today (no PRICES/IMPLIES edges) — use Search to open the market layer.",
                        style={"fontSize": "12px", "color": "#666", "marginTop": "6px"},
                    ),
                ],
            ),
            html.Div(
                style={
                    "display": "flex",
                    "flex": "1",
                    "minHeight": 0,
                },
                children=[
                    # Left controls
                    html.Div(
                        style={
                            "width": "280px",
                            "padding": "12px",
                            "borderRight": "1px solid #ddd",
                            "overflowY": "auto",
                            "background": "#fafafa",
                        },
                        children=[
                            html.Label("Search nodes", style={"fontWeight": 600}),
                            dcc.Input(
                                id="search-input",
                                type="text",
                                placeholder="label, id, or alias…",
                                debounce=True,
                                style={"width": "100%", "marginBottom": "6px"},
                            ),
                            html.Button(
                                "Search",
                                id="search-button",
                                n_clicks=0,
                                style={"width": "100%", "marginBottom": "8px"},
                            ),
                            html.Div(id="search-results"),
                            html.Hr(),
                            html.Label("Node types on canvas", style={"fontWeight": 600}),
                            dcc.Checklist(
                                id="type-filter",
                                options=[{"label": t, "value": t} for t in ALL_NODE_TYPES],
                                value=sorted(TOPOLOGY_NODE_TYPES),
                                style={"fontSize": "12px"},
                            ),
                            html.Br(),
                            html.Label("Min confidence", style={"fontWeight": 600}),
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
                                style={"fontWeight": 600, "marginTop": "8px"},
                            ),
                            dcc.Dropdown(
                                id="inference-filter",
                                options=[
                                    {"label": "(any)", "value": ""},
                                    {"label": "deterministic", "value": "deterministic"},
                                    {"label": "official_bracket", "value": "official_bracket"},
                                    {"label": "llm", "value": "llm"},
                                    {"label": "unknown", "value": "unknown"},
                                ],
                                value="",
                                clearable=False,
                            ),
                            html.Label("Layout", style={"fontWeight": 600, "marginTop": "8px"}),
                            dcc.Dropdown(
                                id="layout-dropdown",
                                options=[
                                    {"label": "breadthfirst (bracket)", "value": "breadthfirst"},
                                    {"label": "cose (force)", "value": "cose"},
                                    {"label": "circle", "value": "circle"},
                                    {"label": "grid", "value": "grid"},
                                    {"label": "concentric", "value": "concentric"},
                                ],
                                value="breadthfirst",
                                clearable=False,
                            ),
                            html.Button(
                                "Reset to topology view",
                                id="reset-button",
                                n_clicks=0,
                                style={"width": "100%", "marginTop": "12px"},
                            ),
                        ],
                    ),
                    # Center canvas
                    html.Div(
                        style={"flex": "1", "minWidth": 0, "position": "relative"},
                        children=[
                            cyto.Cytoscape(
                                id="graph-cyto",
                                elements=initial.to_elements(),
                                stylesheet=default_stylesheet(),
                                layout={
                                    "name": "breadthfirst",
                                    "directed": True,
                                    "padding": 20,
                                    "spacingFactor": 1.2,
                                },
                                style={"width": "100%", "height": "100%"},
                                minZoom=0.1,
                                maxZoom=3,
                                boxSelectionEnabled=True,
                            ),
                        ],
                    ),
                    # Right inspector
                    html.Div(
                        style={
                            "width": "320px",
                            "padding": "12px",
                            "borderLeft": "1px solid #ddd",
                            "overflowY": "auto",
                            "background": "#fafafa",
                        },
                        children=[
                            html.H4("Inspector", style={"marginTop": 0}),
                            html.Div(
                                id="inspector-panel",
                                children=html.P(
                                    "Click a node or edge to inspect its features.",
                                    style={"color": "#666", "fontSize": "13px"},
                                ),
                            ),
                            html.Div(
                                style={"display": "flex", "gap": "8px", "marginTop": "12px"},
                                children=[
                                    html.Button(
                                        "Expand neighbors",
                                        id="expand-button",
                                        n_clicks=0,
                                        disabled=True,
                                    ),
                                    html.Button(
                                        "Remove from canvas",
                                        id="remove-button",
                                        n_clicks=0,
                                        disabled=True,
                                    ),
                                ],
                            ),
                            html.Div(
                                id="action-status",
                                style={"fontSize": "12px", "color": "#666", "marginTop": "8px"},
                            ),
                        ],
                    ),
                ],
            ),
            # Stores
            dcc.Store(id="selected-node-id", data=None),
            dcc.Store(id="selected-edge-id", data=None),
        ],
    )

    register_callbacks(app, settings)
    return app
