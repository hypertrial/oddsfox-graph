"""MATCH identity merge policy for entity resolution.

Isolates dateful-id upgrades, ±1-day fixture coalescing, and official-bracket
preference from the generic fuzzy / slug linker in ``resolution.py``.
"""

from __future__ import annotations

import re
from datetime import date

from oddsgraph.ontology import NodeType
from oddsgraph.schema import CanonicalNode, Node

_DATEFUL_MATCH_RE = re.compile(r"^(match:.+)-(\d{4}-\d{2}-\d{2})$")
NEAR_DATE_MAX_DAY_DELTA = 1


def more_specific_match_id(candidate: str, current: str) -> bool:
    """True when candidate extends current (e.g. label-only → dateful MATCH id)."""
    if not candidate.startswith("match:") or not current.startswith("match:"):
        return False
    return candidate != current and candidate.startswith(current + "-")


def parse_dateful_match_id(match_id: str) -> tuple[str, date] | None:
    """Split ``match:<teams>-YYYY-MM-DD`` into ``(match:<teams>, date)``."""
    matched = _DATEFUL_MATCH_RE.match(match_id)
    if matched is None:
        return None
    try:
        return matched.group(1), date.fromisoformat(matched.group(2))
    except ValueError:
        return None


def near_date_same_fixture(
    existing_id: str,
    candidate_id: str,
    *,
    max_day_delta: int = NEAR_DATE_MAX_DAY_DELTA,
) -> bool:
    """True when two dateful MATCH ids are the same team pair ± one calendar day.

    Polymarket event-slug dates and FIFA ``kickoff_at_utc`` dates often disagree
    by one day for the same fixture; those must coalesce. Month-apart rematches
    stay distinct.
    """
    existing = parse_dateful_match_id(existing_id)
    candidate = parse_dateful_match_id(candidate_id)
    if existing is None or candidate is None:
        return False
    prefix_a, date_a = existing
    prefix_b, date_b = candidate
    if prefix_a != prefix_b:
        return False
    return abs((date_a - date_b).days) <= max_day_delta


def preferred_inference_method(existing: str, incoming: str) -> str:
    """Prefer official_bracket provenance when merging topology + schedule."""
    if incoming == "official_bracket" or existing == "official_bracket":
        return "official_bracket"
    return existing or incoming or "unknown"


def match_id_upgrade_target(
    existing: CanonicalNode,
    node: Node,
    inference_method: str,
) -> str | None:
    """Return a more specific / near-date MATCH id to upgrade to, if any."""
    if node.type != NodeType.MATCH or not node.local_id.startswith("match:"):
        return None
    candidate_id = node.local_id
    existing_id = existing.canonical_id
    if candidate_id == existing_id:
        return None
    if more_specific_match_id(candidate_id, existing_id):
        return candidate_id
    if not near_date_same_fixture(existing_id, candidate_id):
        return None
    # Prefer the official-bracket kickoff date when that fragment is binding.
    if inference_method == "official_bracket":
        return candidate_id
    if existing.inference_method == "official_bracket":
        return None
    existing_parsed = parse_dateful_match_id(existing_id)
    candidate_parsed = parse_dateful_match_id(candidate_id)
    if (
        existing_parsed is not None
        and candidate_parsed is not None
        and candidate_parsed[1] >= existing_parsed[1]
    ):
        return candidate_id
    return None


def match_bind_allowed(existing: CanonicalNode, node: Node) -> bool:
    """Allow MATCH merges for identical IDs, label-only↔dateful, or ±1-day dates.

    Distinct fixtures (same display label, dates farther than one day apart)
    must remain separate even when exact_slug/label would otherwise collide.
    """
    if node.type != NodeType.MATCH:
        return True
    existing_id = existing.canonical_id
    candidate_id = (
        node.local_id if node.local_id.startswith("match:") else existing_id
    )
    if not existing_id.startswith("match:") or not candidate_id.startswith("match:"):
        return True
    if existing_id == candidate_id:
        return True
    if more_specific_match_id(candidate_id, existing_id):
        return True
    if more_specific_match_id(existing_id, candidate_id):
        return True
    if near_date_same_fixture(existing_id, candidate_id):
        return True
    return False


__all__ = [
    "NEAR_DATE_MAX_DAY_DELTA",
    "match_bind_allowed",
    "match_id_upgrade_target",
    "more_specific_match_id",
    "near_date_same_fixture",
    "parse_dateful_match_id",
    "preferred_inference_method",
]
