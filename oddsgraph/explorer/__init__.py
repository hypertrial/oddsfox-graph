"""Local, read-only graph explorer over exported parquet artifacts."""

from __future__ import annotations

from oddsgraph.bracket import ALL_STAGE_LABELS

__all__ = [
    "TOPOLOGY_NODE_TYPES",
    "KNOCKOUT_STAGE_LABELS",
    "VIEW_BRACKET",
    "VIEW_TOPOLOGY",
]

TOPOLOGY_NODE_TYPES: frozenset[str] = frozenset(
    {
        "COMPETITION",
        "STAGE",
        "GROUP",
        "ROUND",
        "MATCH",
        "TEAM",
    }
)

# Knockout match stages only — excludes Group Stage and the Champion pseudo-stage.
KNOCKOUT_STAGE_LABELS: frozenset[str] = frozenset(
    label for label in ALL_STAGE_LABELS if label not in ("Group Stage", "Champion")
)

VIEW_BRACKET = "bracket"
VIEW_TOPOLOGY = "topology"
