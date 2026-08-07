"""Dash HTML helpers for the explorer shell (no callback registration)."""

from __future__ import annotations

from typing import Any

from dash import html

from oddsgraph.explorer.presentation import (
    format_hour_iso,
    format_hour_label,
    format_hour_label_compact,
    phase_at_hour,
    tracker_step_states,
)


def build_tracker_items(hour_epoch: int | None = None) -> list[Any]:
    """Return phase-tracker ``<li>`` children for the selected hour."""
    phase = phase_at_hour(hour_epoch)
    steps = tracker_step_states(phase)
    items: list[Any] = []
    for step in steps:
        classes = f"stage-step is-{step['state']}"
        attrs: dict[str, Any] = {
            "className": classes,
            "role": "listitem",
            **(
                {"aria-current": "step"}
                if step["state"] in {"active", "up-next"}
                else {}
            ),
        }
        items.append(
            html.Li(
                [
                    html.Span(step["label"], className="stage-step-label"),
                    html.Span(
                        step["abbr"],
                        className="stage-step-abbr",
                        **{"aria-hidden": "true"},
                    ),
                ],
                **attrs,
            )
        )
    return items


def build_tracker(
    phase_key: str | None = None,
    hour_epoch: int | None = None,
) -> html.Ol:
    """Return the phase tracker ``<ol>`` element."""
    phase = phase_at_hour(hour_epoch)
    return html.Ol(
        build_tracker_items(hour_epoch),
        id="stage-tracker",
        className="stage-tracker",
        role="list",
        **{"aria-label": "Tournament phases", "data-phase-key": phase_key or phase.key},
    )


def playback_time_children(hour_epoch: int | None) -> list[Any]:
    """Return compact ``<time>`` children for the playback dock."""
    compact = format_hour_label_compact(hour_epoch)
    full = format_hour_label(hour_epoch)
    iso = format_hour_iso(hour_epoch)
    return [
        html.Time(
            compact,
            dateTime=iso or None,
            title=full,
            className="playback-time-value",
        )
    ]


def phase_badge_children(hour_epoch: int | None) -> list[Any]:
    """Return phase badge children for the playback dock."""
    phase = phase_at_hour(hour_epoch)
    return [
        html.Span(phase.label, className="phase-badge-label"),
        html.Span(phase.detail, className="phase-badge-detail") if phase.detail else None,
    ]
