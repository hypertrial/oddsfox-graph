"""Inspector rendering helpers for the local graph explorer."""

from __future__ import annotations

from typing import Any

from dash import html

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

