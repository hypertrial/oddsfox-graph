"""Pure bracket-view helpers shared by explorer data loading and presentation.

Kept free of Dash and phase/timeline UI so ``data.py`` can enrich MATCH nodes
without importing presentation orchestration.
"""

from __future__ import annotations

from oddsgraph.bracket import KNOCKOUT_STAGE_RANK, STAGE_KEY_TO_LABEL
from oddsgraph.bracket_projection import split_match_teams

# Reverse map: stage label -> rank (Final and Third Place both rank 5).
STAGE_LABEL_TO_RANK: dict[str, int] = {
    STAGE_KEY_TO_LABEL[key]: rank for key, rank in KNOCKOUT_STAGE_RANK.items()
}


def short_match_label(label: str) -> str:
    """Return a two-line card label from ``Home vs. Away``."""
    teams = split_match_teams(label)
    if teams is None:
        return label.strip()
    return f"{teams[0]}\n{teams[1]}"


def stage_rank(stage_label: str) -> int:
    """Return knockout rank for a stage label, or 0 if unknown."""
    return STAGE_LABEL_TO_RANK.get(stage_label, 0)


__all__ = [
    "STAGE_LABEL_TO_RANK",
    "short_match_label",
    "stage_rank",
]
