"""Local, read-only graph explorer over exported parquet artifacts."""

from __future__ import annotations

__all__ = ["TOPOLOGY_NODE_TYPES"]

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
