"""Dash callbacks for the local graph explorer."""

from __future__ import annotations

import json
from typing import Any

from dash import ALL, Dash, Input, Output, State, callback_context, html, no_update
from dash.exceptions import PreventUpdate

from oddsgraph.config import Settings
from oddsgraph.explorer import VIEW_BRACKET, VIEW_TOPOLOGY
from oddsgraph.explorer.canvas_actions import (
    CanvasMutation,
    _canvas_callback_outputs,
    _noop_mutation,
    add_search_result,
    expand_neighbors,
    filter_canvas,
    highlight_on_tap,
    load_view,
    open_in_topology,
    remove_from_canvas,
)
from oddsgraph.explorer.data import get_edge, get_node, search_nodes
from oddsgraph.explorer.inspector import (
    _hover_card_children,
    _inspector_sheet,
    _search_results_list,
)
from oddsgraph.explorer.presentation import bracket_layout, topology_layout

# Re-export public canvas helpers so existing test imports keep working.
__all__ = [
    "add_search_result",
    "expand_neighbors",
    "highlight_on_tap",
    "load_view",
    "open_in_topology",
    "register_callbacks",
]

def register_callbacks(app: Dash, settings: Settings) -> None:
    """Wire explorer interactions against ``settings`` artifacts."""

    @app.callback(
        Output("search-results", "children"),
        Input("search-button", "n_clicks"),
        Input("search-input", "n_submit"),
        State("search-input", "value"),
        prevent_initial_call=True,
    )
    def run_search(
        n_clicks: int | None,
        n_submit: int | None,
        query: str | None,
    ) -> Any:
        del n_clicks, n_submit
        if not callback_context.triggered:
            raise PreventUpdate
        rows = search_nodes(settings, query or "", limit=25)
        return _search_results_list(rows)

    @app.callback(
        Output("controls-panel", "className"),
        Output("controls-open", "data"),
        Input("toggle-controls", "n_clicks"),
        State("controls-open", "data"),
        prevent_initial_call=True,
    )
    def toggle_controls(n_clicks: int | None, is_open: bool | None) -> tuple[str, bool]:
        del n_clicks
        next_open = not bool(is_open)
        classes = "explorer-controls is-open" if next_open else "explorer-controls"
        return classes, next_open

    @app.callback(
        Output("inspector-rail", "className"),
        Output("inspector-open", "data"),
        Input("toggle-inspector", "n_clicks"),
        State("inspector-open", "data"),
        prevent_initial_call=True,
    )
    def toggle_inspector(n_clicks: int | None, is_open: bool | None) -> tuple[str, bool]:
        del n_clicks
        next_open = not bool(is_open)
        classes = "explorer-inspector is-open" if next_open else "explorer-inspector"
        return classes, next_open

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

    # --- Canvas mutations split into three registered callbacks ---

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=False),
        Input("view-mode", "value"),
        Input("reset-button", "n_clicks"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        State("skip-view-reload", "data"),
        prevent_initial_call=True,
    )
    def load_or_reset_view(
        view_mode_input: str | None,
        reset_clicks: int | None,
        min_confidence: float | None,
        inference_method: str | None,
        skip_view_reload: bool | None,
    ) -> CanvasMutation:
        del reset_clicks
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]
        conf = float(min_confidence or 0.0)
        method = inference_method or ""
        view_mode = view_mode_input or VIEW_BRACKET
        if triggered.startswith("reset-button"):
            return load_view(
                settings, view_mode, conf, method, reset=True
            )
        if triggered.startswith("view-mode"):
            return load_view(
                settings,
                view_mode,
                conf,
                method,
                skip_view_reload=bool(skip_view_reload),
            )
        raise PreventUpdate

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("type-filter", "value"),
        Input("confidence-filter", "value"),
        Input("inference-filter", "value"),
        Input("graph-cyto", "tapNodeData"),
        State("graph-cyto", "elements"),
        prevent_initial_call=True,
    )
    def highlight_and_filter(
        visible_types: list[str] | None,
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

        if (
            triggered.startswith("type-filter")
            or triggered.startswith("confidence-filter")
            or triggered.startswith("inference-filter")
        ):
            return filter_canvas(elements, visible_types, conf, method)

        raise PreventUpdate

    @app.callback(
        *_canvas_callback_outputs(allow_duplicate=True),
        Input("expand-button", "n_clicks"),
        Input("remove-button", "n_clicks"),
        Input("view-in-topology-button", "n_clicks"),
        Input({"type": "search-result", "index": ALL}, "n_clicks"),
        State("graph-cyto", "elements"),
        State("selected-node-id", "data"),
        State("view-mode", "value"),
        State("type-filter", "value"),
        State("confidence-filter", "value"),
        State("inference-filter", "value"),
        prevent_initial_call=True,
    )
    def expand_search_mutations(
        expand_clicks: int | None,
        remove_clicks: int | None,
        topology_clicks: int | None,
        search_clicks: list[int] | None,
        elements: list[dict[str, Any]] | None,
        selected_node_id: str | None,
        view_mode_state: str | None,
        visible_types: list[str] | None,
        min_confidence: float | None,
        inference_method: str | None,
    ) -> CanvasMutation:
        del expand_clicks, remove_clicks, topology_clicks, search_clicks
        if not callback_context.triggered:
            raise PreventUpdate

        triggered = callback_context.triggered[0]["prop_id"]
        conf = float(min_confidence or 0.0)
        method = inference_method or ""
        view_mode = view_mode_state or VIEW_BRACKET

        if triggered.startswith("view-in-topology-button"):
            return open_in_topology(
                settings,
                selected_node_id,
                conf,
                method,
                current_view_mode=view_mode,
            )

        if triggered.startswith("expand-button"):
            return expand_neighbors(
                settings,
                selected_node_id,
                view_mode,
                elements,
                visible_types,
                conf,
                method,
            )

        if triggered.startswith("remove-button"):
            return remove_from_canvas(
                selected_node_id, elements, visible_types, conf, method
            )

        if "search-result" in triggered:
            prop = triggered.rsplit(".", 1)[0]
            try:
                payload = json.loads(prop)
            except json.JSONDecodeError:
                return (
                    no_update,
                    "Failed to parse search result.",
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                )
            node_id = payload.get("index")
            if not node_id:
                return _noop_mutation()
            if not callback_context.triggered[0]["value"]:
                raise PreventUpdate
            return add_search_result(
                settings,
                str(node_id),
                view_mode,
                elements,
                visible_types,
                conf,
                method,
            )

        raise PreventUpdate

    @app.callback(
        Output("graph-cyto", "layout"),
        Input("layout-dropdown", "value"),
        State("view-mode", "value"),
        prevent_initial_call=True,
    )
    def update_layout(
        layout_name: str | None,
        view_mode: str | None,
    ) -> dict[str, Any]:
        name = layout_name or ("preset" if view_mode != VIEW_TOPOLOGY else "breadthfirst")
        if name == "preset":
            return bracket_layout()
        return topology_layout(name)

    @app.callback(
        Output("inspector-panel", "children"),
        Output("selected-node-id", "data"),
        Output("selected-edge-id", "data"),
        Output("expand-button", "disabled"),
        Output("remove-button", "disabled"),
        Output("view-in-topology-button", "disabled"),
        Input("graph-cyto", "tapNodeData"),
        Input("graph-cyto", "tapEdgeData"),
        prevent_initial_call=True,
    )
    def inspect_selection(
        node_data: dict[str, Any] | None,
        edge_data: dict[str, Any] | None,
    ) -> tuple[Any, Any, Any, bool, bool, bool]:
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]

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
                    False,
                    False,
                )
            return (
                _inspector_sheet("Node", row, stage=stage),
                node_id,
                None,
                False,
                False,
                False,
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
                    True,
                    True,
                )
            return _inspector_sheet("Edge", row), None, edge_id, True, True, True

        raise PreventUpdate
