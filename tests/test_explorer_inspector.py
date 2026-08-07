"""Tests for explorer inspector and hover helpers."""

from __future__ import annotations

import pytest

dash = pytest.importorskip("dash")


def test_hover_card_shows_aligned_teams_and_status() -> None:
    from oddsgraph.explorer.inspector import _hover_card_children

    children, style = _hover_card_children(
        {
            "label": "France vs. Netherlands",
            "stage": "Quarterfinals",
            "home_team": "France",
            "away_team": "Netherlands",
            "home_prob_label": "52%",
            "away_prob_label": "48%",
            "projected": True,
            "projection_method": "stage_conditional",
        }
    )
    assert style["display"] == "block"
    rendered = str(children)
    assert "France" in rendered
    assert "Netherlands" in rendered
    assert "52%" in rendered
    assert "projected" in rendered.lower()


def test_inspector_sheet_prefers_presentation_match_block() -> None:
    from oddsgraph.explorer.inspector import _inspector_sheet

    sheet = _inspector_sheet(
        "Node",
        {
            "canonical_id": "match:france-vs-netherlands",
            "label": "France vs. Netherlands",
            "type": "MATCH",
            "confidence": 1.0,
            "aliases": ["fifa-match-1"],
            "resolution_method": "exact_id",
            "inference_method": "official_bracket",
            "evidence_market_ids": ["m1"],
        },
        stage="Quarterfinals",
        presentation={
            "id": "match:france-vs-netherlands",
            "label": "France vs. Netherlands",
            "stage": "Quarterfinals",
            "home_team": "France",
            "away_team": "Netherlands",
            "home_prob_label": "52%",
            "away_prob_label": "48%",
            "projected": True,
            "projection_method": "stage_conditional",
            "match_start_epoch": 1_783_627_200,
        },
    )
    rendered = str(sheet)
    assert "Match" in rendered
    assert "52%" in rendered
    assert "Graph metadata" in rendered
    assert "Evidence" in rendered
