"""Dash HTML renderers for the mirrored knockout tree UI."""

from __future__ import annotations

from typing import Any, Literal

from dash import dcc, html

from oddsgraph.bracket_projection import format_prob_label
from oddsgraph.explorer.tree import (
    LEFT_ROUNDS_OUT_TO_IN,
    RIGHT_ROUNDS_IN_TO_OUT,
    BracketHalf,
    BracketTree,
    build_knockout_tree,
)

ConnectorDirection = Literal["ltr", "rtl"]
GradeStatus = Literal["correct", "incorrect", "pending", "path-diverged"]


def match_grade_status(data: dict[str, Any]) -> GradeStatus:
    """Map explorer match fields onto status styles for probability text."""
    if data.get("resolved"):
        return "correct"
    if not data.get("probability_available", True):
        return "path-diverged"
    return "pending"


def probability_grade_class(status: GradeStatus | None) -> str:
    if status == "correct":
        return "prob-correct"
    if status == "incorrect":
        return "prob-incorrect"
    if status == "path-diverged":
        return "prob-diverged"
    return "prob-pending"


def _node_data(el: dict[str, Any] | None) -> dict[str, Any]:
    if not el:
        return {}
    return el.get("data") or el


def _is_hidden(el: dict[str, Any] | None) -> bool:
    if not el:
        return False
    return "hidden" in str(el.get("classes") or "").split()


def build_connector_paths(feeder_count: int, direction: ConnectorDirection) -> list[str]:
    """Port of website ``buildConnectorPaths`` / ``connectorPathD``.

    Stems stop short of the viewBox edge so arrowheads can sit on the tip.
    """
    if feeder_count <= 1:
        return []
    pair_count = feeder_count // 2
    paths: list[str] = []
    for pair_index in range(pair_count):
        top_y = ((pair_index * 2 + 0.5) / feeder_count) * 100
        bottom_y = ((pair_index * 2 + 1.5) / feeder_count) * 100
        mid_y = ((pair_index * 2 + 1) / feeder_count) * 100
        if direction == "ltr":
            # Stem ends at x=17; arrow tip reaches x=20 toward the next round.
            paths.append(
                f"M 0 {top_y} H 10 V {mid_y} H 17 M 0 {bottom_y} H 10 V {mid_y}"
            )
        else:
            paths.append(
                f"M 20 {top_y} H 10 V {mid_y} H 3 M 20 {bottom_y} H 10 V {mid_y}"
            )
    return paths


def build_connector_arrow_paths(
    feeder_count: int, direction: ConnectorDirection
) -> list[str]:
    """Filled chevrons at each connector stem tip (points toward the Final)."""
    if feeder_count <= 1:
        return []
    pair_count = feeder_count // 2
    arrows: list[str] = []
    half = 1.35
    for pair_index in range(pair_count):
        mid_y = ((pair_index * 2 + 1) / feeder_count) * 100
        if direction == "ltr":
            arrows.append(
                f"M 16.2 {mid_y - half} L 20 {mid_y} L 16.2 {mid_y + half} Z"
            )
        else:
            arrows.append(
                f"M 3.8 {mid_y - half} L 0 {mid_y} L 3.8 {mid_y + half} Z"
            )
    return arrows


def _connector_svg_markup(
    paths: list[str],
    *,
    arrow_paths: list[str] | None = None,
) -> str:
    """Inline SVG markup (Dash has no html.Svg; Markdown allows HTML)."""
    path_xml = "".join(
        (
            f'<path d="{d}" fill="none" stroke="currentColor" '
            f'stroke-width="0.75" stroke-linejoin="round" '
            f'vector-effect="non-scaling-stroke" />'
        )
        for d in paths
    )
    arrow_xml = "".join(
        (
            f'<path d="{d}" fill="currentColor" stroke="none" '
            f'vector-effect="non-scaling-stroke" />'
        )
        for d in (arrow_paths or [])
    )
    return (
        '<svg class="bracket-connector-svg" viewBox="0 0 20 100" '
        'preserveAspectRatio="none" role="presentation" '
        f'xmlns="http://www.w3.org/2000/svg">{path_xml}{arrow_xml}</svg>'
    )


def render_connector(
    feeder_count: int,
    direction: ConnectorDirection,
    *,
    semi: bool = False,
) -> html.Div:
    """SVG bracket connector between round columns, with progression arrows."""
    if semi:
        if direction == "ltr":
            paths = ["M 0 50 H 17"]
            arrow_paths = ["M 16.2 48.65 L 20 50 L 16.2 51.35 Z"]
        else:
            paths = ["M 20 50 H 3"]
            arrow_paths = ["M 3.8 48.65 L 0 50 L 3.8 51.35 Z"]
        width_class = "bracket-connector is-semi"
    else:
        count = max(feeder_count, 0)
        paths = build_connector_paths(count, direction)
        arrow_paths = build_connector_arrow_paths(count, direction)
        width_class = "bracket-connector"
    return html.Div(
        className=width_class,
        **{"aria-hidden": "true"},
        children=[
            dcc.Markdown(
                _connector_svg_markup(paths, arrow_paths=arrow_paths),
                dangerously_allow_html=True,
                className="bracket-connector-md",
            )
        ],
    )


def _team_row(
    *,
    team_name: str | None,
    flag_url: str | None,
    is_winner: bool | None,
    probability: float | None,
    show_prob: bool,
    grade_status: GradeStatus | None,
    compact: bool,
    odds_tick: str | None = None,
    delta_pp: int | None = None,
) -> html.Div:
    name = team_name or "TBD"
    classes = ["match-team-row"]
    if compact:
        classes.append("is-compact")
    if is_winner is True:
        classes.append("is-winner")
    elif is_winner is False:
        classes.append("is-loser")
    else:
        classes.append("is-neutral")
    if odds_tick == "up":
        classes.append("is-odds-up")
    elif odds_tick == "down":
        classes.append("is-odds-down")
    children: list[Any] = []
    if flag_url:
        children.append(
            html.Img(
                src=flag_url,
                alt="",
                className="match-team-flag",
                **{"aria-hidden": "true"},
            )
        )
    children.append(html.P(name, className="match-team-name"))
    if show_prob:
        prob_classes = ["match-team-prob", probability_grade_class(grade_status)]
        if odds_tick == "up":
            prob_classes.append("is-tick-up")
        elif odds_tick == "down":
            prob_classes.append("is-tick-down")
        prob_children: list[Any] = [format_prob_label(probability)]
        if delta_pp is not None and delta_pp != 0:
            sign = "+" if delta_pp > 0 else ""
            tick_class = "match-prob-delta is-up" if delta_pp > 0 else "match-prob-delta is-down"
            prob_children.append(
                html.Span(
                    f"{sign}{delta_pp}",
                    className=tick_class,
                    **{"aria-hidden": "true"},
                )
            )
        children.append(
            html.P(
                prob_children,
                className=" ".join(prob_classes),
            )
        )
    return html.Div(className=" ".join(classes), children=children)


def render_match_card(
    el: dict[str, Any] | None,
    *,
    emphasis: bool = False,
    compact: bool = False,
) -> html.Fieldset:
    """Render a two-team matchup card with flags and advance probabilities."""
    data = _node_data(el)
    frame = ["match-card"]
    if emphasis:
        frame.append("is-emphasis")
    if compact:
        frame.append("is-compact")
    if _is_hidden(el):
        frame.append("is-hidden")
    if data.get("resolved"):
        frame.append("is-resolved")
    if data.get("just_finished"):
        frame.append("is-just-finished")
    if data.get("projected"):
        frame.append("is-projected")
    if data.get("favorite_flipped"):
        frame.append("is-favorite-flip")
    timeline = data.get("timeline_state")
    if timeline:
        frame.append(f"is-timeline-{timeline}")

    if not el or not data:
        return html.Fieldset(
            className=" ".join(frame),
            **{"data-slot": "match-card", "aria-label": "Match to be determined"},
            children=[
                html.Div("TBD", className="match-team-row is-neutral is-empty"),
                html.Div("TBD", className="match-team-row is-neutral is-empty"),
            ],
        )

    home = data.get("home_team")
    away = data.get("away_team")
    home_prob_raw = data.get("current_home_prob")
    home_prob = float(home_prob_raw) if home_prob_raw is not None else None
    away_prob = None if home_prob is None else 1.0 - home_prob
    grade = match_grade_status(data)
    # Always show a mark when teams are present: numeric % or "—" when unavailable.
    show_prob = bool(home or away)
    just_finished = bool(data.get("just_finished"))
    home_delta_pp = data.get("home_prob_delta_pp")
    home_tick = data.get("odds_tick_home")
    away_tick = data.get("odds_tick_away")
    away_delta_pp = (
        None if home_delta_pp is None else -int(home_delta_pp)
    )

    # Highlight locked/favored side only when a probability or locked winner exists.
    home_wins: bool | None = None
    winner_team = data.get("winner_team") if data.get("resolved") else None
    if winner_team and home and away:
        home_wins = str(winner_team) == str(home)
    elif home_prob is not None:
        home_wins = home_prob >= 0.5

    home_label = str(home) if home else "TBD"
    away_label = str(away) if away else "TBD"
    aria = f"{home_label} vs {away_label}"
    if show_prob:
        aria = (
            f"{aria}; {home_label} {format_prob_label(home_prob)}, "
            f"{away_label} {format_prob_label(away_prob)}"
        )
        if home_delta_pp is not None and int(home_delta_pp) != 0:
            sign = "+" if int(home_delta_pp) > 0 else ""
            aria = f"{aria}; {home_label} {sign}{int(home_delta_pp)} points"
    if just_finished:
        aria = f"{aria}; just finished"

    children: list[Any] = []
    if just_finished:
        children.append(
            html.Span(
                "Just finished",
                className="match-just-finished-badge",
                **{"aria-hidden": "true"},
            )
        )
    children.extend(
        [
            _team_row(
                team_name=str(home) if home else None,
                flag_url=data.get("home_flag"),
                is_winner=home_wins if home else None,
                probability=home_prob,
                show_prob=show_prob,
                grade_status=grade,
                compact=compact,
                odds_tick=str(home_tick) if home_tick else None,
                delta_pp=int(home_delta_pp) if home_delta_pp is not None else None,
            ),
            _team_row(
                team_name=str(away) if away else None,
                flag_url=data.get("away_flag"),
                is_winner=(
                    (None if home_wins is None else (not home_wins)) if away else None
                ),
                probability=away_prob,
                show_prob=show_prob,
                grade_status=grade,
                compact=compact,
                odds_tick=str(away_tick) if away_tick else None,
                delta_pp=away_delta_pp,
            ),
        ]
    )

    return html.Fieldset(
        className=" ".join(frame),
        **{
            "data-slot": "match-card",
            "data-match-id": str(data.get("id") or ""),
            "aria-label": aria,
        },
        children=children,
    )


def _round_header(short_label: str, name: str) -> html.Header:
    return html.Header(
        className="tree-round-header",
        children=[
            html.P(short_label, className="tree-round-short"),
            html.P(name, className="tree-round-name", title=name),
        ],
    )


def _header_spacer() -> html.Div:
    return html.Div(className="tree-header-spacer", **{"aria-hidden": "true"})


def render_tree_side(
    half: BracketHalf,
    direction: ConnectorDirection,
    *,
    region_label: str,
) -> html.Section:
    """One mirrored half of the knockout tree."""
    rounds = LEFT_ROUNDS_OUT_TO_IN if direction == "ltr" else RIGHT_ROUNDS_IN_TO_OUT
    semi_spacer = html.Div(
        className="bracket-connector is-semi tree-header-pad",
        **{"aria-hidden": "true"},
        children=[_header_spacer()],
    )

    header_row: list[Any] = []
    if direction == "rtl":
        header_row.append(semi_spacer)
    for index, (round_id, short_label, name) in enumerate(rounds):
        header_row.append(
            html.Div(
                className="tree-round-header-cell",
                children=[_round_header(short_label, name)],
            )
        )
        if index < len(rounds) - 1:
            header_row.append(
                html.Div(
                    className="bracket-connector tree-header-pad",
                    **{"aria-hidden": "true"},
                    children=[_header_spacer()],
                )
            )
    if direction == "ltr":
        header_row.append(semi_spacer)

    body_row: list[Any] = []
    if direction == "rtl":
        body_row.append(render_connector(1, "rtl", semi=True))
    for index, (round_id, short_label, name) in enumerate(rounds):
        del short_label, name
        matches = half.by_round(round_id)
        body_row.append(
            html.Div(
                className="tree-column",
                **{"data-round-id": round_id},
                children=[
                    html.Section(
                        className="tree-column-body",
                        **{"aria-label": f"{round_id} matches"},
                        children=[
                            html.Div(
                                className=(
                                    "tree-card-slot is-r32"
                                    if round_id == "r32"
                                    else "tree-card-slot"
                                ),
                                children=[render_match_card(m, compact=True)],
                            )
                            for m in matches
                        ],
                    )
                ],
            )
        )
        if index < len(rounds) - 1:
            next_round_id = rounds[index + 1][0]
            if direction == "ltr":
                feeder_count = len(matches)
            else:
                feeder_count = len(half.by_round(next_round_id))
            body_row.append(render_connector(feeder_count, direction))
    if direction == "ltr":
        body_row.append(render_connector(1, "ltr", semi=True))

    return html.Section(
        className="tree-side",
        **{"aria-label": region_label, "data-slot": "tree-side"},
        children=[
            html.Div(className="tree-side-headers", children=header_row),
            html.Div(className="tree-side-body", children=body_row),
        ],
    )


def render_final_column(
    tree: BracketTree,
    *,
    compact: bool = True,
) -> html.Section:
    """Final + champion + third-place center column."""
    champion = tree.champion
    final_data = _node_data(tree.final)
    actual_champion = None
    if final_data.get("resolved") and final_data.get("winner_team"):
        actual_champion = str(final_data["winner_team"])
    champion_flag = None
    if tree.final:
        if champion and champion == final_data.get("home_team"):
            champion_flag = final_data.get("home_flag")
        elif champion and champion == final_data.get("away_team"):
            champion_flag = final_data.get("away_flag")

    classes = ["final-column"]
    if compact:
        classes.append("is-compact")

    grade_text = None
    grade_class = "text-muted"
    if actual_champion and champion:
        if actual_champion == champion:
            grade_text = "Correct"
            grade_class = "prob-correct"
        else:
            grade_text = "Incorrect"
            grade_class = "prob-incorrect"

    champion_block = html.Div(
        className="champion-card",
        children=[
            html.P("Predicted champion", className="champion-label"),
            html.Div(
                className="champion-row",
                children=[
                    html.Span("🏆", className="champion-trophy", **{"aria-hidden": "true"}),
                    (
                        html.Img(
                            src=champion_flag,
                            alt="",
                            className="champion-flag",
                            **{"aria-hidden": "true"},
                        )
                        if champion_flag
                        else None
                    ),
                    html.P(champion or "TBD", className="champion-name"),
                ],
            ),
            html.Div(
                className="actual-champion",
                children=[
                    html.P("Actual champion", className="actual-champion-label"),
                    html.P(
                        actual_champion or "Pending",
                        className=(
                            "actual-champion-name"
                            if actual_champion
                            else "actual-champion-name is-pending"
                        ),
                    ),
                    (
                        html.P(grade_text, className=f"actual-champion-grade {grade_class}")
                        if grade_text
                        else None
                    ),
                ],
            ),
        ],
    )

    return html.Section(
        className=" ".join(classes),
        **{
            "aria-label": "World Cup final, champion, and third-place match",
            "data-slot": "final-column",
        },
        children=[
            html.Div(
                className="final-column-title",
                children=[html.P("Final", className="final-title")],
            ),
            html.Div(
                className="final-column-grid",
                children=[
                    render_match_card(tree.final, emphasis=True, compact=compact),
                    champion_block,
                    html.Div(
                        children=[
                            html.P("Third place", className="third-place-label"),
                            render_match_card(tree.third_place, compact=compact),
                        ]
                    ),
                ],
            ),
        ],
    )


def render_legend() -> html.Div:
    """Pill-badge status legend for probability colors."""
    pills = [
        ("Resolved", "legend-pill is-correct"),
        ("Pending", "legend-pill is-pending"),
        ("Diverged", "legend-pill is-diverged"),
    ]
    return html.Div(
        className="probability-legend",
        children=[
            html.P(
                [
                    html.Span("%", className="legend-accent"),
                    " = advance probability for each team. Green marks a locked "
                    "result, cyan a projected matchup, and gray when odds are "
                    "unavailable or the knockout path diverged.",
                ],
                className="legend-copy",
            ),
            html.Ul(
                className="legend-pills",
                **{"aria-label": "Probability colour key"},
                children=[
                    html.Li(
                        className=cls,
                        children=[
                            html.Span(className="legend-dot", **{"aria-hidden": "true"}),
                            label,
                        ],
                    )
                    for label, cls in pills
                ],
            ),
        ],
    )


def render_stacked_rounds(tree: BracketTree) -> html.Div:
    """Narrow-viewport stacked rounds layout (ports KnockoutStackedRounds)."""
    sections: list[Any] = [
        html.Div(
            className="stacked-final-wrap",
            children=[
                html.P(
                    "Stage 1 of 6 — Final & podium",
                    className="stacked-stage-eyebrow",
                ),
                render_final_column(tree, compact=False),
            ],
        )
    ]
    rounds_in_to_out = list(reversed(LEFT_ROUNDS_OUT_TO_IN))
    for index, (round_id, short_label, name) in enumerate(rounds_in_to_out):
        matches = [*tree.left.by_round(round_id), *tree.right.by_round(round_id)]
        sections.append(
            html.Article(
                className="stacked-round-section",
                **{"data-round-id": round_id},
                children=[
                    html.Div(
                        className="stacked-round-header",
                        children=[
                            html.Div(
                                children=[
                                    html.P(
                                        f"Stage {index + 2} of 6",
                                        className="stacked-stage-eyebrow",
                                    ),
                                    html.H3(name, className="stacked-round-title"),
                                    html.P(
                                        f"{len(matches)} matches",
                                        className="stacked-round-count",
                                    ),
                                ]
                            ),
                            html.Span(short_label, className="stacked-round-badge"),
                        ],
                    ),
                    html.Div(
                        className=f"stacked-round-grid is-{round_id}",
                        children=[render_match_card(m) for m in matches],
                    ),
                ],
            )
        )
    return html.Div(className="stacked-knockout", children=sections)


def render_mirrored_tree(tree: BracketTree) -> html.Div:
    """Desktop mirrored two-halves tree."""
    return html.Div(
        className="knockout-tree",
        **{"data-slot": "knockout-tree"},
        children=[
            html.Section(
                className="knockout-tree-inner",
                **{"aria-label": "Knockout bracket tree"},
                children=[
                    html.Div(
                        className="knockout-tree-row",
                        children=[
                            render_tree_side(
                                tree.left,
                                "ltr",
                                region_label="Left bracket half toward final",
                            ),
                            html.Div(
                                className="final-column-wrap",
                                children=[render_final_column(tree, compact=True)],
                            ),
                            render_tree_side(
                                tree.right,
                                "rtl",
                                region_label="Right bracket half toward final",
                            ),
                        ],
                    )
                ],
            )
        ],
    )


def render_bracket_tree(
    tree: BracketTree,
    *,
    include_legend: bool = True,
) -> html.Div:
    """Top-level bracket: mirrored tree + stacked fallback (CSS media query)."""
    children: list[Any] = []
    if include_legend:
        children.append(
            html.Header(
                className="bracket-section-header",
                children=[
                    html.P(
                        "Stages 1–5 of 6 · Knockout stage",
                        className="bracket-eyebrow",
                    ),
                    html.H2(
                        "Final back to the Round of 32",
                        className="bracket-title",
                    ),
                    render_legend(),
                ],
            )
        )
    children.append(
        html.Div(
            className="bracket-desktop",
            children=[render_mirrored_tree(tree)],
        )
    )
    children.append(
        html.Div(
            className="bracket-mobile",
            children=[render_stacked_rounds(tree)],
        )
    )
    return html.Div(
        className="bracket-root",
        **{"aria-label": "World Cup 2026 bracket"},
        children=children,
    )


def elements_to_bracket_children(
    elements: list[dict[str, Any]],
    *,
    include_legend: bool = True,
) -> html.Div:
    """Build knockout tree HTML children from bracket element dicts."""
    tree = build_knockout_tree(elements)
    return render_bracket_tree(tree, include_legend=include_legend)


__all__ = [
    "build_connector_arrow_paths",
    "build_connector_paths",
    "elements_to_bracket_children",
    "match_grade_status",
    "probability_grade_class",
    "render_bracket_tree",
    "render_connector",
    "render_final_column",
    "render_legend",
    "render_match_card",
    "render_mirrored_tree",
    "render_stacked_rounds",
    "render_tree_side",
]
