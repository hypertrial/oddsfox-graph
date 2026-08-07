"""Local, read-only graph explorer over exported parquet artifacts."""

from __future__ import annotations

from oddsgraph.bracket import ALL_STAGE_LABELS

__all__ = [
    "KNOCKOUT_STAGE_LABELS",
]

# Knockout match stages only — excludes Group Stage and the Champion pseudo-stage.
KNOCKOUT_STAGE_LABELS: frozenset[str] = frozenset(
    label for label in ALL_STAGE_LABELS if label not in ("Group Stage", "Champion")
)
