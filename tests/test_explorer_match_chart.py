"""Tests for match odds sparkline SVG and expand-chart figure builders."""

from __future__ import annotations

from oddsgraph.explorer.match_chart import (
    SPARKLINE_AWAY_COLOR,
    SPARKLINE_HOME_COLOR,
    build_match_chart_figure,
    build_sparkline_svg_markup,
)


def test_sparkline_svg_includes_polyline_and_color() -> None:
    points = [(100, 0.4), (200, 0.55), (300, 0.7)]
    markup = build_sparkline_svg_markup(points, SPARKLINE_HOME_COLOR)
    assert "polyline" in markup
    assert SPARKLINE_HOME_COLOR in markup
    assert "circle" in markup
    assert 'width="60"' in markup


def test_sparkline_svg_empty_points_does_not_raise() -> None:
    markup = build_sparkline_svg_markup([], SPARKLINE_AWAY_COLOR)
    assert "svg" in markup
    assert "polyline" not in markup


def test_match_chart_figure_two_traces_and_colors() -> None:
    home = [(100, 0.55), (200, 0.60), (300, 0.70)]
    away = [(100, 0.45), (200, 0.40), (300, 0.30)]
    fig = build_match_chart_figure(
        home,
        away,
        home_label="France",
        away_label="Argentina",
        match_start_epoch=100,
        match_end_epoch=300,
    )
    assert len(fig.data) == 2
    assert fig.data[0].name == "France"
    assert fig.data[1].name == "Argentina"
    assert fig.data[0].line.color == SPARKLINE_HOME_COLOR
    assert fig.data[1].line.color == SPARKLINE_AWAY_COLOR
    assert list(fig.data[0].x) == [0.0, (200 - 100) / 3600.0, (300 - 100) / 3600.0]


def test_match_chart_empty_shows_placeholder() -> None:
    fig = build_match_chart_figure(
        [],
        [],
        home_label="A",
        away_label="B",
    )
    assert len(fig.data) == 0
    assert fig.layout.annotations
    assert "Not enough" in fig.layout.annotations[0].text
