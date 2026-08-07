"""Dash callbacks for the local graph explorer."""

from __future__ import annotations

import json
from typing import Any

from dash import ALL, Dash, Input, Output, State, callback_context, html, no_update
from dash.exceptions import PreventUpdate

from oddsgraph.config import Settings
from oddsgraph.explorer import TOPOLOGY_NODE_TYPES, VIEW_BRACKET, VIEW_TOPOLOGY
from oddsgraph.explorer.data import (
    GraphSlice,
    bracket_elements,
    get_edge,
    get_node,
    node_element,
    node_neighbors,
    search_nodes,
    topology_elements,
)
from oddsgraph.explorer.filters import (
    apply_filters,
    clear_interaction_classes,
    is_edge,
    merge_elements,
    node_types_in_elements,
    union_types,
)
from oddsgraph.explorer.presentation import (
    apply_path_highlight,
    bracket_layout,
    stylesheet_for,
    topology_layout,
)

CanvasMutation = tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any]


def _noop_mutation() -> CanvasMutation:
    return (
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def _truncation_note(slice_: GraphSlice, limit: int = 300) -> str:
    if slice_.truncated:
        return f" Truncated to {limit} incident edges."
    return ""


def _view_payload(
    settings: Settings, view_mode: str
) -> tuple[GraphSlice, list[str], str, str]:
    """Return (slice, type_filter, layout_name, status) for a view mode."""
    if view_mode == VIEW_TOPOLOGY:
        return (
            topology_elements(settings),
            sorted(TOPOLOGY_NODE_TYPES),
            "breadthfirst",
            "Loaded full topology view.",
        )
    return (
        bracket_elements(settings),
        ["MATCH"],
        "preset",
        "Loaded knockout bracket view.",
    )


def highlight_on_tap(
    elements: list[dict[str, Any]] | None,
    tap_node: dict[str, Any] | None,
) -> CanvasMutation | None:
    """Apply path highlight for a tapped node. Returns None if tap is empty."""
    if not tap_node or not tap_node.get("id"):
        return None
    focus_id = str(tap_node["id"])
    highlighted = apply_path_highlight(elements or [], focus_id)
    return (
        highlighted,
        f"Highlighted path through {focus_id}.",
        no_update,
        no_update,
        no_update,
        no_update,
        focus_id,
        None,
        no_update,
    )


def filter_canvas(
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Re-apply type/confidence/inference filters without reloading the graph."""
    return (
        apply_filters(elements or [], visible_types, min_confidence, inference_method),
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def load_view(
    settings: Settings,
    view_mode: str,
    min_confidence: float,
    inference_method: str,
    *,
    skip_view_reload: bool = False,
    reset: bool = False,
) -> CanvasMutation:
    """Load bracket/topology view, honoring skip-view-reload on mode echo."""
    if not reset and skip_view_reload:
        return (
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            False,
        )
    slice_, next_types, layout_name, status = _view_payload(settings, view_mode)
    if reset:
        status = f"Reset to {view_mode} view."
    return (
        apply_filters(slice_.to_elements(), next_types, min_confidence, inference_method),
        status,
        next_types,
        layout_name,
        no_update,
        stylesheet_for(view_mode),
        None,
        None,
        False,
    )


def open_in_topology(
    settings: Settings,
    selected_node_id: str | None,
    min_confidence: float,
    inference_method: str,
    *,
    current_view_mode: str = VIEW_BRACKET,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Merge selected node + neighbors into full topology and switch view."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node first.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    topo = topology_elements(settings)
    row = get_node(settings, selected_node_id)
    extras = GraphSlice(
        nodes=[node_element(row)] if row else [],
        edges=[],
    )
    neighbors = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
    merged = merge_elements(topo.to_elements(), extras.to_elements())
    merged = merge_elements(merged, neighbors.to_elements())
    next_types = union_types(
        sorted(TOPOLOGY_NODE_TYPES),
        node_types_in_elements(extras.nodes + neighbors.nodes),
    )
    highlighted = apply_path_highlight(
        apply_filters(merged, next_types, min_confidence, inference_method),
        selected_node_id,
    )
    status = (
        f"Opened {selected_node_id} in Full topology."
        f"{_truncation_note(neighbors, neighbor_limit)}"
    )
    # Only suppress the view-mode echo reload when we actually change modes.
    skip_reload = current_view_mode != VIEW_TOPOLOGY
    return (
        highlighted,
        status,
        next_types,
        "breadthfirst",
        VIEW_TOPOLOGY,
        stylesheet_for(VIEW_TOPOLOGY),
        selected_node_id,
        None,
        skip_reload,
    )


def expand_neighbors(
    settings: Settings,
    selected_node_id: str | None,
    view_mode: str,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
    *,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Expand 1-hop neighbors; bracket view switches to topology first."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node before expanding.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    if view_mode == VIEW_BRACKET:
        topo = topology_elements(settings)
        neighbors = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
        merged = merge_elements(topo.to_elements(), neighbors.to_elements())
        next_types = union_types(
            sorted(TOPOLOGY_NODE_TYPES),
            node_types_in_elements(neighbors.nodes),
        )
        status = (
            f"Expanded {selected_node_id} in Full topology "
            f"(bracket view stays MATCH-only)."
            f"{_truncation_note(neighbors, neighbor_limit)}"
        )
        return (
            apply_filters(merged, next_types, min_confidence, inference_method),
            status,
            next_types,
            "breadthfirst",
            VIEW_TOPOLOGY,
            stylesheet_for(VIEW_TOPOLOGY),
            selected_node_id,
            None,
            True,
        )
    slice_ = node_neighbors(settings, selected_node_id, limit=neighbor_limit)
    if not slice_.edges and len(slice_.nodes) <= 1:
        return (
            no_update,
            (
                f"No neighbors for {selected_node_id}. "
                "Covered templates bridge via REFERS_TO/PRICES; residual "
                "market types may still lack a topology bridge."
            ),
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    merged = merge_elements(elements, slice_.to_elements())
    next_types = union_types(visible_types, node_types_in_elements(slice_.nodes))
    status = (
        f"Expanded neighbors of {selected_node_id}."
        f"{_truncation_note(slice_, neighbor_limit)}"
    )
    return (
        apply_filters(merged, next_types, min_confidence, inference_method),
        status,
        next_types,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
        no_update,
    )


def remove_from_canvas(
    selected_node_id: str | None,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
) -> CanvasMutation:
    """Remove the selected node and its incident edges from the canvas."""
    if not selected_node_id:
        return (
            no_update,
            "Select a node before removing.",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
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
    cleaned = clear_interaction_classes(
        apply_filters(remaining, visible_types, min_confidence, inference_method),
        keep_hidden=True,
    )
    return (
        cleaned,
        f"Removed {selected_node_id} from canvas.",
        no_update,
        no_update,
        no_update,
        no_update,
        None,
        None,
        no_update,
    )


def add_search_result(
    settings: Settings,
    node_id: str,
    view_mode: str,
    elements: list[dict[str, Any]] | None,
    visible_types: list[str] | None,
    min_confidence: float,
    inference_method: str,
    *,
    neighbor_limit: int = 300,
) -> CanvasMutation:
    """Add a search-hit node (and neighbors); bracket switches to topology."""
    row = get_node(settings, node_id)
    if row is None:
        return (
            no_update,
            f"Node not found: {node_id}",
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )
    if view_mode == VIEW_BRACKET:
        topo = topology_elements(settings)
        slice_ = GraphSlice(nodes=[node_element(row)], edges=[])
        neighbors = node_neighbors(settings, node_id, limit=neighbor_limit)
        merged = merge_elements(topo.to_elements(), slice_.to_elements())
        merged = merge_elements(merged, neighbors.to_elements())
        next_types = union_types(
            sorted(TOPOLOGY_NODE_TYPES),
            node_types_in_elements(slice_.nodes + neighbors.nodes),
        )
        status = (
            f"Opened {node_id} in Full topology."
            f"{_truncation_note(neighbors, neighbor_limit)}"
        )
        return (
            apply_filters(merged, next_types, min_confidence, inference_method),
            status,
            next_types,
            "breadthfirst",
            VIEW_TOPOLOGY,
            stylesheet_for(VIEW_TOPOLOGY),
            node_id,
            None,
            True,
        )
    slice_ = GraphSlice(nodes=[node_element(row)], edges=[])
    neighbors = node_neighbors(settings, node_id, limit=neighbor_limit)
    merged = merge_elements(elements, slice_.to_elements())
    merged = merge_elements(merged, neighbors.to_elements())
    added_types = node_types_in_elements(slice_.nodes + neighbors.nodes)
    next_types = union_types(visible_types, added_types)
    status = (
        f"Added {node_id} and its neighbors to the canvas."
        f"{_truncation_note(neighbors, neighbor_limit)}"
    )
    return (
        apply_filters(merged, next_types, min_confidence, inference_method),
        status,
        next_types,
        no_update,
        no_update,
        no_update,
        node_id,
        None,
        no_update,
    )


def _format_value(value: Any, *, limit: int = 8) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return "[]"
        if len(value) <= limit:
            return ", ".join(str(v) for v in value)
        head = ", ".join(str(v) for v in value[:limit])
        return f"{head}, … (+{len(value) - limit} more)"
    return str(value)


def _chip(text: str, *, accent: bool = False, warn: bool = False) -> html.Span:
    classes = "chip"
    if accent:
        classes += " chip-accent"
    if warn:
        classes += " chip-warn"
    return html.Span(text, className=classes)


def _kv_table(rows: list[tuple[str, str]]) -> html.Table:
    body = []
    for key, value in rows:
        body.append(
            html.Tr(
                [
                    html.Th(key, scope="row"),
                    html.Td(value),
                ]
            )
        )
    return html.Table(body, className="inspector-table")


def _inspector_sheet(
    kind: str,
    row: dict[str, Any],
    *,
    stage: str | None = None,
) -> html.Div:
    """Structured inspector for nodes/edges."""
    label = str(row.get("label") or row.get("edge_type") or kind)
    canonical = str(
        row.get("canonical_id")
        or (
            f"{row.get('source_id')}|{row.get('edge_type')}|{row.get('target_id')}"
            if kind == "Edge"
            else ""
        )
    )
    node_type = str(row.get("type") or row.get("edge_type") or "")
    confidence = row.get("confidence")
    chips = []
    if node_type:
        chips.append(_chip(node_type, accent=True))
    if stage:
        chips.append(_chip(stage, warn=True))
    if confidence is not None:
        chips.append(_chip(f"confidence {confidence}"))
    method = row.get("inference_method")
    if method:
        chips.append(_chip(str(method)))

    identity_rows: list[tuple[str, str]] = []
    if kind == "Node":
        identity_rows = [
            ("canonical_id", canonical),
            ("label", str(row.get("label") or "")),
            ("aliases", _format_value(row.get("aliases") or [])),
        ]
        if stage:
            identity_rows.append(("stage", stage))
    else:
        identity_rows = [
            ("source", str(row.get("source_id") or "")),
            ("target", str(row.get("target_id") or "")),
            ("edge_type", str(row.get("edge_type") or "")),
            ("evidence_text", str(row.get("evidence_text") or "")),
        ]

    provenance_rows = [
        ("resolution_method", str(row.get("resolution_method") or "—")),
        ("inference_method", str(row.get("inference_method") or "—")),
        ("confidence", str(row.get("confidence") if row.get("confidence") is not None else "—")),
    ]

    evidence = row.get("evidence_market_ids") or []
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    evidence_block: list[Any] = [
        html.P(
            f"{evidence_count} evidence market id(s)",
            className="evidence-summary",
        )
    ]
    if evidence_count:
        evidence_block.append(
            html.Details(
                [
                    html.Summary("Show evidence market ids"),
                    html.P(
                        _format_value(evidence, limit=40),
                        className="evidence-summary",
                    ),
                ]
            )
        )

    return html.Div(
        [
            html.H3(label, className="inspector-title"),
            html.P(canonical, className="inspector-subtitle"),
            html.Div(chips, className="chip-row"),
            html.Div(
                className="inspector-section",
                children=[html.H5("Identity"), _kv_table(identity_rows)],
            ),
            html.Div(
                className="inspector-section",
                children=[html.H5("Provenance"), _kv_table(provenance_rows)],
            ),
            html.Div(
                className="inspector-section",
                children=[html.H5("Evidence"), *evidence_block],
            ),
        ]
    )


def _search_results_list(rows: list[dict[str, Any]]) -> html.Div:
    if not rows:
        return html.Div("No matches.", className="panel-hint")
    items = []
    for row in rows:
        items.append(
            html.Button(
                f"{row['type']}: {row['label']}",
                id={"type": "search-result", "index": row["canonical_id"]},
                n_clicks=0,
                className="search-result-btn",
                title=row["canonical_id"],
                type="button",
                role="listitem",
            )
        )
    return html.Div(items, className="search-results")


def _hover_card_children(data: dict[str, Any] | None) -> tuple[Any, dict[str, str]]:
    if not data:
        return [], {"display": "none"}
    label = data.get("label") or data.get("id") or ""
    stage = data.get("stage") or data.get("type") or ""
    conf = data.get("confidence")
    meta_bits = [str(stage)] if stage else []
    if conf is not None:
        meta_bits.append(f"confidence {conf}")
    return (
        [
            html.P(str(label), className="hover-card-title"),
            html.P(" · ".join(meta_bits), className="hover-card-meta"),
        ],
        {"display": "block"},
    )


def _canvas_callback_outputs(*, allow_duplicate: bool = False) -> list[Output]:
    kwargs = {"allow_duplicate": True} if allow_duplicate else {}
    return [
        Output("graph-cyto", "elements", **kwargs),
        Output("action-status", "children", **kwargs),
        Output("type-filter", "value", **kwargs),
        Output("layout-dropdown", "value", **kwargs),
        Output("view-mode", "value", **kwargs),
        Output("graph-cyto", "stylesheet", **kwargs),
        Output("selected-node-id", "data", allow_duplicate=True),
        Output("selected-edge-id", "data", allow_duplicate=True),
        Output("skip-view-reload", "data", **kwargs),
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
