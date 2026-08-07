"""Inspector rendering helpers for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import html

from oddsgraph.explorer.presentation import format_hour_label


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


def _status_label(data: dict[str, Any] | None) -> str:
    if not data:
        return "—"
    if data.get("is_champion"):
        return "Champion"
    if data.get("is_third_place_winner"):
        return "3rd place"
    if data.get("resolved"):
        return "Resolved"
    if data.get("projected"):
        return "Projected"
    return "Scheduled"


def _inspector_sheet(
    kind: str,
    row: dict[str, Any],
    *,
    stage: str | None = None,
    presentation: dict[str, Any] | None = None,
) -> html.Div:
    """Structured inspector for nodes/edges."""
    presentation = presentation or {}
    label = str(
        presentation.get("label")
        or row.get("label")
        or row.get("edge_type")
        or kind
    )
    canonical = str(
        row.get("canonical_id")
        or (
            f"{row.get('source_id')}|{row.get('edge_type')}|{row.get('target_id')}"
            if kind == "Edge"
            else presentation.get("id")
            or ""
        )
    )
    node_type = str(row.get("type") or row.get("edge_type") or presentation.get("type") or "")
    confidence = row.get("confidence")
    stage_label = stage or presentation.get("stage")
    chips = []
    if node_type:
        chips.append(_chip(node_type, accent=True))
    if stage_label:
        chips.append(_chip(str(stage_label), warn=True))
    status = _status_label(presentation)
    if status != "—":
        chips.append(_chip(status))
    if confidence is not None:
        chips.append(_chip(f"confidence {confidence}"))
    method = presentation.get("projection_method") or row.get("inference_method")
    if method:
        chips.append(_chip(str(method)))

    match_block: list[Any] = []
    if kind == "Node" and (
        presentation.get("home_team")
        or presentation.get("away_team")
        or presentation.get("home_prob_label")
    ):
        home = str(presentation.get("home_team") or "TBD")
        away = str(presentation.get("away_team") or "TBD")
        home_prob = str(presentation.get("home_prob_label") or "—")
        away_prob = str(presentation.get("away_prob_label") or "—")
        kickoff = presentation.get("match_start_epoch")
        end = presentation.get("match_end_epoch")
        winner = presentation.get("winner_team")
        match_rows = [
            ("status", status),
            ("stage", str(stage_label or "—")),
            ("kickoff", format_hour_label(kickoff) if kickoff is not None else "—"),
            ("full time", format_hour_label(end) if end is not None else "—"),
            ("winner", str(winner or "—")),
            ("projection", str(presentation.get("projection_method") or "—")),
        ]
        match_block = [
            html.Div(
                className="inspector-section",
                children=[
                    html.H5("Match"),
                    html.Ul(
                        className="team-odds-list",
                        children=[
                            html.Li([html.Span(home), html.Span(home_prob)]),
                            html.Li([html.Span(away), html.Span(away_prob)]),
                        ],
                    ),
                    _kv_table(match_rows),
                ],
            )
        ]

    identity_rows: list[tuple[str, str]] = []
    if kind == "Node":
        identity_rows = [
            ("canonical_id", canonical),
            ("label", str(row.get("label") or presentation.get("label") or "")),
            ("aliases", _format_value(row.get("aliases") or [])),
        ]
        if stage_label:
            identity_rows.append(("stage", str(stage_label)))
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
        (
            "confidence",
            str(row.get("confidence") if row.get("confidence") is not None else "—"),
        ),
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
            *match_block,
            html.Details(
                className="help-details",
                open=False,
                children=[
                    html.Summary("Graph metadata"),
                    html.Div(
                        className="inspector-section",
                        children=[html.H5("Identity"), _kv_table(identity_rows)],
                    ),
                    html.Div(
                        className="inspector-section",
                        children=[html.H5("Provenance"), _kv_table(provenance_rows)],
                    ),
                ],
            ),
            html.Details(
                className="help-details",
                open=False,
                children=[
                    html.Summary("Evidence"),
                    html.Div(className="inspector-section", children=evidence_block),
                ],
            ),
        ]
    )


def _hover_card_children(data: dict[str, Any] | None) -> tuple[Any, dict[str, str]]:
    if not data:
        return [], {"display": "none"}
    label = data.get("label") or data.get("id") or ""
    stage = data.get("stage") or data.get("type") or ""
    home = data.get("home_team")
    away = data.get("away_team")
    home_prob = data.get("home_prob_label") or "—"
    away_prob = data.get("away_prob_label") or "—"
    method = data.get("projection_method")
    meta_bits = [str(stage)] if stage else []
    meta_bits.append(_status_label(data).lower())
    if method and method not in {"resolved", "direct_advance"}:
        meta_bits.append(str(method).replace("_", " "))
    children: list[Any] = [
        html.P(str(label), className="hover-card-title"),
    ]
    if home or away:
        children.append(
            html.Ul(
                className="hover-card-teams",
                children=[
                    html.Li(
                        [
                            html.Span(str(home or "TBD")),
                            html.Span(str(home_prob)),
                        ]
                    ),
                    html.Li(
                        [
                            html.Span(str(away or "TBD")),
                            html.Span(str(away_prob)),
                        ]
                    ),
                ],
            )
        )
    children.append(html.P(" · ".join(meta_bits), className="hover-card-meta"))
    return children, {"display": "block"}
