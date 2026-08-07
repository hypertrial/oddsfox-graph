"""Tests for knockout tree HTML render helpers."""

from __future__ import annotations

import pytest

from oddsgraph.explorer.tree import build_knockout_tree
from oddsgraph.explorer.tree_render import (
    build_connector_paths,
    elements_to_bracket_children,
    match_grade_status,
    render_match_card,
)

dash = pytest.importorskip("dash")


def _match(
    match_id: str,
    stage: str,
    *,
    fifa: int | None = None,
    home: str | None = "Brazil",
    away: str | None = "France",
    home_prob: float | None = 0.6,
    resolved: bool = False,
) -> dict:
    aliases = [f"fifa-match-{fifa}"] if fifa is not None else []
    return {
        "data": {
            "id": match_id,
            "type": "MATCH",
            "stage": stage,
            "label": f"{home} vs. {away}" if home and away else match_id,
            "aliases": aliases,
            "home_team": home,
            "away_team": away,
            "current_home_prob": home_prob,
            "resolved": resolved,
            "projected": not resolved,
            "probability_available": home_prob is not None,
            "home_flag": "/assets/flags/br.svg",
            "away_flag": "/assets/flags/fr.svg",
            "winner_team": home if resolved else None,
        },
        "classes": "MATCH",
    }


def _edge(source: str, target: str) -> dict:
    return {
        "data": {
            "id": f"{source}|ADVANCES_TO|{target}",
            "source": source,
            "target": target,
            "edge_type": "ADVANCES_TO",
        },
        "classes": "ADVANCES_TO",
    }


def test_build_connector_paths_ltr_and_rtl() -> None:
    ltr = build_connector_paths(4, "ltr")
    rtl = build_connector_paths(4, "rtl")
    assert len(ltr) == 2
    assert len(rtl) == 2
    assert ltr[0].startswith("M 0")
    assert rtl[0].startswith("M 20")
    assert build_connector_paths(1, "ltr") == []


def test_match_grade_status_mapping() -> None:
    assert match_grade_status({"resolved": True}) == "correct"
    assert match_grade_status({"projected": True, "probability_available": False}) == (
        "path-diverged"
    )
    assert match_grade_status({"projected": True}) == "pending"


def test_render_match_card_shows_both_probs_and_avoids_false_winner() -> None:
    with_prob = _match("m1", "Round of 32", fifa=1, home_prob=0.6)
    card = render_match_card(with_prob, compact=True)
    text = str(card)
    assert "60%" in text
    assert "40%" in text
    assert "is-winner" in text

    unavailable = _match(
        "m2",
        "Third Place",
        fifa=99,
        home_prob=None,
        resolved=False,
    )
    unavailable["data"]["probability_available"] = False
    unavailable["data"]["projected"] = True
    card2 = render_match_card(unavailable, compact=True)
    text2 = str(card2)
    assert "—" in text2
    assert "is-winner" not in text2
    assert "is-neutral" in text2


def test_render_match_card_marks_just_finished() -> None:
    finished = _match("m1", "Round of 32", fifa=1, home_prob=1.0, resolved=True)
    finished["data"]["just_finished"] = True
    finished["data"]["winner_team"] = "Brazil"
    card = render_match_card(finished, compact=True)
    text = str(card)
    assert "is-just-finished" in text
    assert "Just finished" in text
    assert "just finished" in text

    settled = _match("m2", "Round of 32", fifa=2, home_prob=1.0, resolved=True)
    settled["data"]["just_finished"] = False
    settled["data"]["winner_team"] = "Brazil"
    settled_text = str(render_match_card(settled, compact=True))
    assert "is-just-finished" not in settled_text
    assert "Just finished" not in settled_text


def test_render_match_card_in_tree_smoke() -> None:
    elements = [
        _match("l-sf", "Semifinals", fifa=30),
        _match("r-sf", "Semifinals", fifa=31, home="Spain", away="Germany"),
        _match(
            "final",
            "Final",
            fifa=100,
            home="Brazil",
            away="Spain",
            home_prob=0.55,
            resolved=True,
        ),
        _match("third", "Third Place", fifa=99, home="France", away="Germany"),
        _edge("l-sf", "final"),
        _edge("r-sf", "final"),
    ]
    card = render_match_card(elements[0], compact=True)
    assert "match-card" in card.className
    assert "is-compact" in card.className

    tree = build_knockout_tree(elements)
    assert tree.final is not None
    assert tree.left.sf and tree.right.sf

    children = elements_to_bracket_children(elements)
    rendered = str(children)
    assert "bracket-root" in rendered
    assert "knockout-tree" in rendered
    assert "stacked-knockout" in rendered
    assert "Final" in rendered
    assert "Brazil" in rendered
