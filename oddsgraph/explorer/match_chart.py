"""Chart helpers for match odds sparklines and the expand-chart modal.

Inline sparklines use lightweight SVG (cheap to re-render on every scrub).
The click-to-expand modal uses Plotly. No Dash dependency here.
"""

from __future__ import annotations

from typing import Any, Sequence

import plotly.graph_objects as go

# Match the explorer CSS tokens (--brand-accent / orange accent).
SPARKLINE_HOME_COLOR = "#00e5ff"
SPARKLINE_AWAY_COLOR = "#fb923c"
CHART_AXIS_COLOR = "#94a3b8"
CHART_GRID_COLOR = "rgba(51, 65, 85, 0.55)"
CHART_PAPER_BG = "rgba(0,0,0,0)"
CHART_PLOT_BG = "rgba(0,0,0,0)"

_SPARKLINE_WIDTH = 60
_SPARKLINE_HEIGHT = 20
_SPARKLINE_PAD_Y = 2.0


def build_sparkline_svg_markup(
    points: Sequence[tuple[int, float]],
    color: str,
) -> str:
    """Return inline SVG markup for a compact probability sparkline."""
    if not points:
        return (
            f'<svg class="match-team-sparkline-svg" width="{_SPARKLINE_WIDTH}" '
            f'height="{_SPARKLINE_HEIGHT}" viewBox="0 0 {_SPARKLINE_WIDTH} '
            f'{_SPARKLINE_HEIGHT}" role="presentation" aria-hidden="true" '
            f'xmlns="http://www.w3.org/2000/svg"></svg>'
        )

    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span = (x_max - x_min) or 1.0
    y_span = (y_max - y_min) or 0.1
    y_lo = y_min - y_span * 0.15
    y_hi = y_max + y_span * 0.15
    y_range = (y_hi - y_lo) or 1.0
    drawable_h = _SPARKLINE_HEIGHT - 2 * _SPARKLINE_PAD_Y

    def _xy(hour: float, prob: float) -> tuple[float, float]:
        x = ((hour - x_min) / x_span) * (_SPARKLINE_WIDTH - 2) + 1
        # Invert y so higher probability is toward the top.
        y = _SPARKLINE_PAD_Y + (1.0 - (prob - y_lo) / y_range) * drawable_h
        return x, y

    coords = [_xy(h, p) for h, p in zip(xs, ys)]
    poly = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
    last_x, last_y = coords[-1]
    return (
        f'<svg class="match-team-sparkline-svg" width="{_SPARKLINE_WIDTH}" '
        f'height="{_SPARKLINE_HEIGHT}" viewBox="0 0 {_SPARKLINE_WIDTH} '
        f'{_SPARKLINE_HEIGHT}" role="presentation" aria-hidden="true" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{poly}" />'
        f'<circle cx="{last_x:.2f}" cy="{last_y:.2f}" r="2.2" fill="{color}" />'
        f"</svg>"
    )


def _empty_figure(*, height: int = 220) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=CHART_PAPER_BG,
        plot_bgcolor=CHART_PLOT_BG,
        margin=dict(l=0, r=0, t=0, b=0),
        height=height,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, range=[0, 1]),
        showlegend=False,
        annotations=[
            dict(
                text="Not enough odds data yet",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(color=CHART_AXIS_COLOR, size=12),
            )
        ],
    )
    return fig


def build_match_chart_figure(
    home_points: Sequence[tuple[int, float]],
    away_points: Sequence[tuple[int, float]],
    *,
    home_label: str,
    away_label: str,
    home_color: str = SPARKLINE_HOME_COLOR,
    away_color: str = SPARKLINE_AWAY_COLOR,
    match_start_epoch: int | None = None,
    match_end_epoch: int | None = None,
) -> go.Figure:
    """Build a two-line head-to-head odds chart for the click-to-expand modal."""
    if not home_points and not away_points:
        return _empty_figure()

    fig = go.Figure()
    use_match_local = match_start_epoch is not None

    def _x_values(points: Sequence[tuple[int, float]]) -> list[Any]:
        if use_match_local:
            start = int(match_start_epoch)  # type: ignore[arg-type]
            return [(h - start) / 3600.0 for h, _ in points]
        return [h for h, _ in points]

    if home_points:
        fig.add_trace(
            go.Scatter(
                x=_x_values(home_points),
                y=[p for _, p in home_points],
                mode="lines",
                name=home_label,
                line=dict(color=home_color, width=2),
                hovertemplate=f"{home_label}: %{{y:.0%}}<extra></extra>",
            )
        )
    if away_points:
        fig.add_trace(
            go.Scatter(
                x=_x_values(away_points),
                y=[p for _, p in away_points],
                mode="lines",
                name=away_label,
                line=dict(color=away_color, width=2),
                hovertemplate=f"{away_label}: %{{y:.0%}}<extra></extra>",
            )
        )

    xaxis: dict[str, Any]
    if use_match_local:
        tickvals: list[float] = [0.0]
        ticktext: list[str] = ["Kickoff"]
        if match_end_epoch is not None:
            duration_h = (int(match_end_epoch) - int(match_start_epoch)) / 3600.0  # type: ignore[arg-type]
            if duration_h > 0:
                tickvals.append(duration_h)
                ticktext.append("Full-time")
        xaxis = dict(
            title=None,
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            color=CHART_AXIS_COLOR,
            gridcolor=CHART_GRID_COLOR,
            zeroline=False,
            showline=True,
            linecolor=CHART_GRID_COLOR,
        )
    else:
        xaxis = dict(
            title=None,
            color=CHART_AXIS_COLOR,
            gridcolor=CHART_GRID_COLOR,
            zeroline=False,
            showline=True,
            linecolor=CHART_GRID_COLOR,
            type="linear",
            tickformat="d",
        )

    fig.update_layout(
        paper_bgcolor=CHART_PAPER_BG,
        plot_bgcolor=CHART_PLOT_BG,
        margin=dict(l=48, r=16, t=24, b=48),
        height=280,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(color=CHART_AXIS_COLOR, size=11),
        ),
        xaxis=xaxis,
        yaxis=dict(
            title="Win probability",
            range=[0, 1],
            tickformat=".0%",
            color=CHART_AXIS_COLOR,
            gridcolor=CHART_GRID_COLOR,
            zeroline=False,
            showline=True,
            linecolor=CHART_GRID_COLOR,
        ),
        font=dict(color=CHART_AXIS_COLOR, family="Inter, system-ui, sans-serif"),
        hovermode="x unified",
    )
    return fig


__all__ = [
    "SPARKLINE_AWAY_COLOR",
    "SPARKLINE_HOME_COLOR",
    "build_match_chart_figure",
    "build_sparkline_svg_markup",
]
