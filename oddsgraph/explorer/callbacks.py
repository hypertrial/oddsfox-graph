"""Dash callbacks for the local graph explorer."""

from __future__ import annotations

import json
from typing import Any

from dash import ALL, Dash, Input, Output, State, callback_context, html, no_update
from dash.exceptions import PreventUpdate

from oddsgraph.config import Settings
from oddsgraph.explorer import TOPOLOGY_NODE_TYPES
from oddsgraph.explorer.data import (
    GraphSlice,
    get_edge,
    get_node,
    node_element,
    node_neighbors,
    search_nodes,
    topology_elements,
)
from oddsgraph.explorer.filters import (
    apply_filters,
    is_edge,
    merge_elements,
    node_types_in_elements,
    union_types,
)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return "[]"
        if len(value) <= 8:
            return ", ".join(str(v) for v in value)
        head = ", ".join(str(v) for v in value[:8])
        return f"{head}, … (+{len(value) - 8} more)"
    return str(value)


def _inspector_table(title: str, row: dict[str, Any]) -> html.Div:
    preferred_order = [
        "canonical_id",
        "type",
        "label",
        "aliases",
        "confidence",
        "evidence_market_ids",
        "resolution_method",
        "inference_method",
        "source_id",
        "target_id",
        "edge_type",
        "evidence_text",
    ]
    keys = [k for k in preferred_order if k in row]
    keys.extend(sorted(k for k in row.keys() if k not in keys))

    rows = []
    for key in keys:
        rows.append(
            html.Tr(
                [
                    html.Td(
                        key,
                        style={
                            "fontWeight": 600,
                            "verticalAlign": "top",
                            "paddingRight": "8px",
                            "whiteSpace": "nowrap",
                        },
                    ),
                    html.Td(
                        _format_value(row[key]),
                        style={"wordBreak": "break-word", "fontSize": "12px"},
                    ),
                ]
            )
        )
    return html.Div(
        [
            html.H5(title, style={"margin": "0 0 8px 0"}),
            html.Table(rows, style={"width": "100%", "borderCollapse": "collapse"}),
        ]
    )


def _search_results_list(rows: list[dict[str, Any]]) -> html.Div:
    if not rows:
        return html.Div("No matches.", style={"fontSize": "12px", "color": "#666"})
    items = []
    for row in rows:
        items.append(
            html.Button(
                f"{row['type']}: {row['label']}",
                id={"type": "search-result", "index": row["canonical_id"]},
                n_clicks=0,
                style={
                    "display": "block",
                    "width": "100%",
                    "textAlign": "left",
                    "marginBottom": "4px",
                    "fontSize": "12px",
                    "padding": "4px 6px",
                },
                title=row["canonical_id"],
            )
        )
    return html.Div(items)


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
        if not callback_context.triggered:
            raise PreventUpdate
        rows = search_nodes(settings, query or "", limit=25)
        return _search_results_list(rows)

    @app.callback(
        Output("graph-cyto", "elements"),
        Output("action-status", "children"),
        Output("type-filter", "value"),
        Input("reset-button", "n_clicks"),
        Input("expand-button", "n_clicks"),
        Input("remove-button", "n_clicks"),
        Input({"type": "search-result", "index": ALL}, "n_clicks"),
        Input("type-filter", "value"),
        Input("confidence-filter", "value"),
        Input("inference-filter", "value"),
        State("graph-cyto", "elements"),
        State("selected-node-id", "data"),
        prevent_initial_call=True,
    )
    def mutate_elements(
        reset_clicks: int | None,
        expand_clicks: int | None,
        remove_clicks: int | None,
        search_clicks: list[int] | None,
        visible_types: list[str] | None,
        min_confidence: float | None,
        inference_method: str | None,
        elements: list[dict[str, Any]] | None,
        selected_node_id: str | None,
    ) -> tuple[Any, Any, Any]:
        del reset_clicks, expand_clicks, remove_clicks, search_clicks
        if not callback_context.triggered:
            raise PreventUpdate

        triggered = callback_context.triggered[0]["prop_id"]
        conf = float(min_confidence or 0.0)
        method = inference_method or ""

        if (
            triggered.startswith("type-filter")
            or triggered.startswith("confidence-filter")
            or triggered.startswith("inference-filter")
        ):
            return (
                apply_filters(elements or [], visible_types, conf, method),
                no_update,
                no_update,
            )

        if triggered.startswith("reset-button"):
            slice_ = topology_elements(settings)
            topo_types = sorted(TOPOLOGY_NODE_TYPES)
            return (
                apply_filters(slice_.to_elements(), topo_types, conf, method),
                "Reset to topology view.",
                topo_types,
            )

        if triggered.startswith("expand-button"):
            if not selected_node_id:
                return no_update, "Select a node before expanding.", no_update
            slice_ = node_neighbors(settings, selected_node_id, limit=300)
            if not slice_.edges and len(slice_.nodes) <= 1:
                return (
                    no_update,
                    (
                        f"No neighbors for {selected_node_id}. "
                        "Topology and market layers are disconnected "
                        "(no PRICES/IMPLIES edges yet)."
                    ),
                    no_update,
                )
            merged = merge_elements(elements, slice_.to_elements())
            next_types = union_types(visible_types, node_types_in_elements(slice_.nodes))
            return (
                apply_filters(merged, next_types, conf, method),
                f"Expanded neighbors of {selected_node_id}.",
                next_types,
            )

        if triggered.startswith("remove-button"):
            if not selected_node_id:
                return no_update, "Select a node before removing.", no_update
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
            return (
                apply_filters(remaining, visible_types, conf, method),
                f"Removed {selected_node_id} from canvas.",
                no_update,
            )

        if "search-result" in triggered:
            prop = triggered.rsplit(".", 1)[0]
            try:
                payload = json.loads(prop)
            except json.JSONDecodeError:
                return no_update, "Failed to parse search result.", no_update
            node_id = payload.get("index")
            if not node_id:
                return no_update, no_update, no_update
            if not callback_context.triggered[0]["value"]:
                raise PreventUpdate
            row = get_node(settings, node_id)
            if row is None:
                return no_update, f"Node not found: {node_id}", no_update
            slice_ = GraphSlice(nodes=[node_element(row)], edges=[])
            neighbors = node_neighbors(settings, node_id, limit=300)
            merged = merge_elements(elements, slice_.to_elements())
            merged = merge_elements(merged, neighbors.to_elements())
            added_types = node_types_in_elements(slice_.nodes + neighbors.nodes)
            next_types = union_types(visible_types, added_types)
            return (
                apply_filters(merged, next_types, conf, method),
                f"Added {node_id} and its neighbors to the canvas.",
                next_types,
            )

        raise PreventUpdate

    @app.callback(
        Output("graph-cyto", "layout"),
        Input("layout-dropdown", "value"),
        prevent_initial_call=True,
    )
    def update_layout(layout_name: str | None) -> dict[str, Any]:
        name = layout_name or "cose"
        layout: dict[str, Any] = {"name": name, "animate": True, "padding": 20}
        if name == "breadthfirst":
            layout["directed"] = True
            layout["spacingFactor"] = 1.2
        return layout

    @app.callback(
        Output("inspector-panel", "children"),
        Output("selected-node-id", "data"),
        Output("selected-edge-id", "data"),
        Output("expand-button", "disabled"),
        Output("remove-button", "disabled"),
        Input("graph-cyto", "tapNodeData"),
        Input("graph-cyto", "tapEdgeData"),
        prevent_initial_call=True,
    )
    def inspect_selection(
        node_data: dict[str, Any] | None,
        edge_data: dict[str, Any] | None,
    ) -> tuple[Any, Any, Any, bool, bool]:
        if not callback_context.triggered:
            raise PreventUpdate
        triggered = callback_context.triggered[0]["prop_id"]

        if triggered.startswith("graph-cyto.tapNodeData") and node_data:
            node_id = node_data.get("id")
            row = get_node(settings, node_id) if node_id else None
            if row is None:
                return (
                    html.P(f"Node not found: {node_id}"),
                    node_id,
                    None,
                    False,
                    False,
                )
            return _inspector_table("Node", row), node_id, None, False, False

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
                    html.P(f"Edge not found: {edge_id}"),
                    None,
                    edge_id,
                    True,
                    True,
                )
            return _inspector_table("Edge", row), None, edge_id, True, True

        raise PreventUpdate
