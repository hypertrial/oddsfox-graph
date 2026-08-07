"""Entity resolution from fragment-local nodes to canonical graph nodes."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz, process

from oddsgraph import ids
from oddsgraph.config import Settings
from oddsgraph.ontology import NodeType
from oddsgraph.schema import CanonicalNode, GraphFragment, Node

DEFAULT_COMPETITION_SLUG = "world-cup-2026"
_DATEFUL_MATCH_RE = re.compile(r"^(match:.+)-(\d{4}-\d{2}-\d{2})$")
_NEAR_DATE_MAX_DAY_DELTA = 1


@dataclass
class ResolutionState:
    canonical_nodes: dict[str, CanonicalNode] = field(default_factory=dict)
    local_to_canonical: dict[str, str] = field(default_factory=dict)
    tier_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _FuzzyIndex:
    labels_by_type: dict[NodeType, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    ids_by_type: dict[NodeType, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def add(self, node_type: NodeType, label: str, canonical_id: str) -> None:
        self.labels_by_type[node_type].append(label)
        self.ids_by_type[node_type].append(canonical_id)

    def best_match(
        self,
        node_type: NodeType,
        label: str,
        threshold: int,
    ) -> tuple[str | None, int]:
        labels = self.labels_by_type.get(node_type)
        if not labels:
            return None, 0
        result = process.extractOne(
            label,
            labels,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )
        if result is None:
            return None, 0
        _, score, index = result
        return self.ids_by_type[node_type][index], score


@dataclass
class _ResolutionIndexes:
    by_slug: dict[tuple[NodeType, str], CanonicalNode] = field(default_factory=dict)
    by_label: dict[tuple[NodeType, str], CanonicalNode] = field(default_factory=dict)
    by_alias: dict[tuple[NodeType, str], CanonicalNode] = field(default_factory=dict)
    fuzzy_index: _FuzzyIndex = field(default_factory=_FuzzyIndex)
    evidence_sets: dict[str, set[str]] = field(default_factory=dict)


def _competition_slug_from_fragments(fragments: list[GraphFragment]) -> str:
    for fragment in fragments:
        for node in fragment.nodes:
            if node.type == NodeType.COMPETITION:
                return ids.slugify(node.label)
    return DEFAULT_COMPETITION_SLUG


def _polymarket_canonical_id(node: Node) -> str | None:
    """Tier 1: exact Polymarket identifier only."""
    if node.type == NodeType.EVENT:
        if node.local_id.startswith("event:"):
            return node.local_id
        if node.evidence_market_ids:
            return None
        return ids.event_id(node.local_id.replace("event:", ""))
    if node.type == NodeType.MARKET:
        if node.local_id.startswith("market:"):
            return node.local_id
        if node.evidence_market_ids:
            return ids.market_id(node.evidence_market_ids[0])
    if node.type == NodeType.OUTCOME:
        if node.local_id.startswith("outcome:"):
            return node.local_id
        if node.evidence_market_ids:
            return ids.outcome_id(node.evidence_market_ids[0], node.label)
    return None


def _suggested_canonical_id(node: Node, competition_slug: str) -> str:
    """Deterministic canonical ID for inferred entity types."""
    if node.type == NodeType.TEAM:
        return ids.team_id(node.label)
    if node.type == NodeType.COMPETITION:
        return ids.competition_id(node.label)
    if node.type == NodeType.STAGE:
        return ids.stage_id(competition_slug, node.label)
    if node.type == NodeType.GROUP:
        return ids.group_id(competition_slug, node.label)
    if node.type == NodeType.ROUND:
        return ids.round_id(competition_slug, node.label)
    if node.type == NodeType.MATCH:
        # Prefer builder local_ids (dateful) over label-only IDs.
        if node.local_id.startswith("match:"):
            return node.local_id
        return ids.match_id(node.label)
    if node.type == NodeType.CONSTRAINT:
        # Constraint IDs are fully qualified by the proposition compiler.
        if node.local_id.startswith("constraint:"):
            return node.local_id
        return ids.constraint_id(node.label)
    if node.type == NodeType.EVENT:
        return ids.event_id(node.local_id.replace("event:", ""))
    if node.type == NodeType.MARKET and node.evidence_market_ids:
        return ids.market_id(node.evidence_market_ids[0])
    if node.type == NodeType.OUTCOME and node.evidence_market_ids:
        return ids.outcome_id(node.evidence_market_ids[0], node.label)
    return f"{node.type.value.lower()}:{ids.slugify(node.label)}"


def _register_tier(state: ResolutionState, tier: str) -> None:
    state.tier_counts[tier] = state.tier_counts.get(tier, 0) + 1


def _prepare_team_node(node: Node) -> Node:
    """Canonicalize TEAM display names and retain the raw label as an alias."""
    if node.type != NodeType.TEAM:
        return node
    canonical = ids.canonical_team_name(node.label)
    if canonical == node.label:
        return node
    aliases = sorted({*node.aliases, node.label})
    return node.model_copy(update={"label": canonical, "aliases": aliases})


def _team_code_maps_to_label(code: str, label: str) -> bool:
    mapped = ids.load_team_codes().get(code.lower())
    if not mapped:
        return False
    return ids.normalize_label(ids.canonical_team_name(mapped)) == ids.normalize_label(
        label
    )


def _team_alias_compatible(alias: str, node_label: str, existing_label: str) -> bool:
    """Reject overloaded short codes that map to a different team than both sides."""
    if alias.lower() not in ids.load_team_codes():
        return True
    return _team_code_maps_to_label(alias, node_label) and _team_code_maps_to_label(
        alias, existing_label
    )


def _index_aliases(
    canonical: CanonicalNode,
    aliases: list[str],
    indexes: _ResolutionIndexes,
) -> None:
    for alias in aliases:
        indexes.by_alias[(canonical.type, ids.normalize_label(alias))] = canonical


def _materialize_evidence(
    canonical_id: str,
    indexes: _ResolutionIndexes,
) -> list[str]:
    return sorted(indexes.evidence_sets.get(canonical_id, set()))


def _more_specific_match_id(candidate: str, current: str) -> bool:
    """True when candidate extends current (e.g. label-only → dateful MATCH id)."""
    if not candidate.startswith("match:") or not current.startswith("match:"):
        return False
    return candidate != current and candidate.startswith(current + "-")


def _parse_dateful_match_id(match_id: str) -> tuple[str, date] | None:
    """Split ``match:<teams>-YYYY-MM-DD`` into ``(match:<teams>, date)``."""
    matched = _DATEFUL_MATCH_RE.match(match_id)
    if matched is None:
        return None
    try:
        return matched.group(1), date.fromisoformat(matched.group(2))
    except ValueError:
        return None


def _near_date_same_fixture(
    existing_id: str,
    candidate_id: str,
    *,
    max_day_delta: int = _NEAR_DATE_MAX_DAY_DELTA,
) -> bool:
    """True when two dateful MATCH ids are the same team pair ± one calendar day.

    Polymarket event-slug dates and FIFA ``kickoff_at_utc`` dates often disagree
    by one day for the same fixture; those must coalesce. Month-apart rematches
    stay distinct.
    """
    existing = _parse_dateful_match_id(existing_id)
    candidate = _parse_dateful_match_id(candidate_id)
    if existing is None or candidate is None:
        return False
    prefix_a, date_a = existing
    prefix_b, date_b = candidate
    if prefix_a != prefix_b:
        return False
    return abs((date_a - date_b).days) <= max_day_delta


def _preferred_inference_method(existing: str, incoming: str) -> str:
    """Prefer official_bracket provenance when merging topology + schedule."""
    if incoming == "official_bracket" or existing == "official_bracket":
        return "official_bracket"
    return existing or incoming or "unknown"


def _match_id_upgrade_target(
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
    if _more_specific_match_id(candidate_id, existing_id):
        return candidate_id
    if not _near_date_same_fixture(existing_id, candidate_id):
        return None
    # Prefer the official-bracket kickoff date when that fragment is binding.
    if inference_method == "official_bracket":
        return candidate_id
    if existing.inference_method == "official_bracket":
        return None
    existing_parsed = _parse_dateful_match_id(existing_id)
    candidate_parsed = _parse_dateful_match_id(candidate_id)
    if (
        existing_parsed is not None
        and candidate_parsed is not None
        and candidate_parsed[1] >= existing_parsed[1]
    ):
        return candidate_id
    return None


def _match_bind_allowed(existing: CanonicalNode, node: Node) -> bool:
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
    if _more_specific_match_id(candidate_id, existing_id):
        return True
    if _more_specific_match_id(existing_id, candidate_id):
        return True
    if _near_date_same_fixture(existing_id, candidate_id):
        return True
    return False


def _remap_fuzzy_canonical_id(
    fuzzy: _FuzzyIndex, node_type: NodeType, old_id: str, new_id: str
) -> None:
    ids_list = fuzzy.ids_by_type.get(node_type)
    if not ids_list:
        return
    for i, cid in enumerate(ids_list):
        if cid == old_id:
            ids_list[i] = new_id


def _upgrade_match_canonical_id(
    state: ResolutionState,
    existing: CanonicalNode,
    new_id: str,
    indexes: _ResolutionIndexes,
) -> CanonicalNode:
    """Re-key a MATCH canonical node when a more specific local_id arrives later."""
    old_id = existing.canonical_id
    if old_id == new_id or new_id in state.canonical_nodes:
        return existing
    old_evidence = indexes.evidence_sets.pop(old_id, set())
    indexes.evidence_sets.setdefault(new_id, set()).update(old_evidence)
    upgraded = existing.model_copy(update={"canonical_id": new_id})
    del state.canonical_nodes[old_id]
    state.canonical_nodes[new_id] = upgraded
    for local_id, cid in list(state.local_to_canonical.items()):
        if cid == old_id:
            state.local_to_canonical[local_id] = new_id
    label_slug = ids.slugify(upgraded.label)
    indexes.by_slug[(upgraded.type, label_slug)] = upgraded
    indexes.by_label[(upgraded.type, label_slug)] = upgraded
    _index_aliases(upgraded, list(upgraded.aliases) + [upgraded.label], indexes)
    _remap_fuzzy_canonical_id(indexes.fuzzy_index, upgraded.type, old_id, new_id)
    return upgraded


def _merge_canonical(
    state: ResolutionState,
    existing: CanonicalNode,
    node: Node,
    method: str,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str = "unknown",
) -> CanonicalNode:
    evidence = indexes.evidence_sets.setdefault(existing.canonical_id, set())
    evidence.update(existing.evidence_market_ids)
    evidence.update(node.evidence_market_ids)
    previous_aliases = set(existing.aliases) | {existing.label}
    merged_aliases = sorted(set(existing.aliases) | set(node.aliases) | {node.label})
    merged = existing.model_copy(
        update={
            "confidence": max(existing.confidence, node.confidence),
            # Evidence lists are finalized once at the end of resolve_fragments.
            "aliases": merged_aliases,
            "resolution_method": method,
            "inference_method": _preferred_inference_method(
                existing.inference_method, inference_method
            ),
        }
    )
    label_slug = ids.slugify(merged.label)
    indexes.by_slug[(merged.type, label_slug)] = merged
    indexes.by_label[(merged.type, label_slug)] = merged
    _index_aliases(merged, merged_aliases + [merged.label], indexes)
    if node.label not in previous_aliases:
        indexes.fuzzy_index.add(merged.type, node.label, merged.canonical_id)
    state.canonical_nodes[existing.canonical_id] = merged
    return merged


def _register_canonical(
    state: ResolutionState,
    canonical_id: str,
    node: Node,
    method: str,
    inference_method: str,
    indexes: _ResolutionIndexes,
) -> None:
    indexes.evidence_sets[canonical_id] = set(node.evidence_market_ids)
    canonical = CanonicalNode(
        canonical_id=canonical_id,
        type=node.type,
        label=node.label,
        aliases=list(node.aliases),
        confidence=node.confidence,
        evidence_market_ids=list(node.evidence_market_ids),
        resolution_method=method,
        inference_method=inference_method,
    )
    state.canonical_nodes[canonical_id] = canonical
    state.local_to_canonical[node.local_id] = canonical_id
    label_slug = ids.slugify(node.label)
    indexes.by_slug[(node.type, label_slug)] = canonical
    indexes.by_label[(node.type, label_slug)] = canonical
    _index_aliases(canonical, list(node.aliases) + [node.label], indexes)
    indexes.fuzzy_index.add(node.type, node.label, canonical_id)


def _bind_existing(
    state: ResolutionState,
    existing: CanonicalNode,
    node: Node,
    method: str,
    tier: str,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str = "unknown",
) -> bool:
    """Bind ``node`` onto ``existing`` when the merge is allowed.

    Returns False when a MATCH dateful-id conflict blocks the bind so the
    caller can fall through to registering a new canonical node.
    """
    if not _match_bind_allowed(existing, node):
        return False
    upgrade_to = _match_id_upgrade_target(existing, node, inference_method)
    if upgrade_to is not None:
        existing = _upgrade_match_canonical_id(state, existing, upgrade_to, indexes)
    merged = _merge_canonical(
        state,
        existing,
        node,
        method,
        indexes,
        inference_method=inference_method,
    )
    state.local_to_canonical[node.local_id] = merged.canonical_id
    _register_tier(state, tier)
    return True


def _try_exact_id(
    state: ResolutionState,
    node: Node,
    method: str,
    indexes: _ResolutionIndexes,
) -> bool:
    polymarket_id = _polymarket_canonical_id(node)
    if not polymarket_id:
        return False
    if polymarket_id in state.canonical_nodes:
        return _bind_existing(
            state,
            state.canonical_nodes[polymarket_id],
            node,
            "exact_id",
            "exact_id",
            indexes,
            inference_method=method,
        )
    _register_canonical(
        state, polymarket_id, node, "exact_id", method, indexes
    )
    _register_tier(state, "exact_id")
    return True


def _try_exact_slug(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str,
) -> bool:
    slug_key = (node.type, ids.slugify(node.label))
    existing = indexes.by_slug.get(slug_key)
    if existing is None:
        return False
    return _bind_existing(
        state,
        existing,
        node,
        "exact_slug",
        "exact_slug",
        indexes,
        inference_method=inference_method,
    )


def _try_exact_label(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str,
) -> bool:
    # slugify == normalize_label; reuse the cached slug from exact_slug lookups.
    label_key = (node.type, ids.normalize_label(node.label))
    existing = indexes.by_label.get(label_key)
    if existing is None:
        return False
    return _bind_existing(
        state,
        existing,
        node,
        "exact_label",
        "exact_label",
        indexes,
        inference_method=inference_method,
    )


def _try_alias(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str,
) -> bool:
    for alias in [node.label] + list(node.aliases):
        alias_key = (node.type, ids.normalize_label(alias))
        existing = indexes.by_alias.get(alias_key)
        if existing is not None and (
            node.type != NodeType.TEAM
            or _team_alias_compatible(alias, node.label, existing.label)
        ):
            if _bind_existing(
                state,
                existing,
                node,
                "alias",
                "alias",
                indexes,
                inference_method=inference_method,
            ):
                return True
            continue
        if node.type != NodeType.TEAM:
            continue
        if not _team_code_maps_to_label(alias, node.label):
            continue
        for code_alias in ids.team_aliases_from_code(alias):
            code_key = (node.type, ids.normalize_label(code_alias))
            existing = indexes.by_alias.get(code_key)
            if existing is None:
                continue
            if _bind_existing(
                state,
                existing,
                node,
                "alias",
                "alias",
                indexes,
                inference_method=inference_method,
            ):
                return True
    return False


def _try_fuzzy(
    state: ResolutionState,
    node: Node,
    settings: Settings,
    indexes: _ResolutionIndexes,
    *,
    inference_method: str,
) -> bool:
    best_canonical_id, _ = indexes.fuzzy_index.best_match(
        node.type,
        node.label,
        settings.fuzzy_threshold,
    )
    if best_canonical_id is None:
        return False
    return _bind_existing(
        state,
        state.canonical_nodes[best_canonical_id],
        node,
        "fuzzy",
        "fuzzy",
        indexes,
        inference_method=inference_method,
    )


def _register_new(
    state: ResolutionState,
    node: Node,
    method: str,
    competition_slug: str,
    indexes: _ResolutionIndexes,
) -> None:
    new_id = _suggested_canonical_id(node, competition_slug)
    if new_id in state.canonical_nodes:
        _bind_existing(
            state,
            state.canonical_nodes[new_id],
            node,
            "new_entity",
            "new_entity",
            indexes,
            inference_method=method,
        )
        # If bind is rejected (incompatible MATCH pair), leave the existing
        # occupant and skip registering a conflicting alias for this local id.
        return
    _register_canonical(state, new_id, node, "new_entity", method, indexes)
    _register_tier(state, "new_entity")


def _finalize_evidence(state: ResolutionState, indexes: _ResolutionIndexes) -> None:
    for canonical_id, canonical in list(state.canonical_nodes.items()):
        evidence = _materialize_evidence(canonical_id, indexes)
        # Skip model_copy when evidence is already the finalized sorted list.
        if evidence == canonical.evidence_market_ids:
            continue
        state.canonical_nodes[canonical_id] = canonical.model_copy(
            update={"evidence_market_ids": evidence}
        )


def resolve_fragments(
    fragments: list[GraphFragment],
    settings: Settings,
    inference_method: str = "unknown",
    inference_methods: list[str] | None = None,
) -> ResolutionState:
    state = ResolutionState()
    all_nodes: list[tuple[Node, str]] = []

    for idx, fragment in enumerate(fragments):
        method = (
            inference_methods[idx]
            if inference_methods is not None and idx < len(inference_methods)
            else inference_method
        )
        for node in fragment.nodes:
            all_nodes.append((_prepare_team_node(node), method))

    competition_slug = _competition_slug_from_fragments(fragments)
    indexes = _ResolutionIndexes()

    for node, method in all_nodes:
        if _try_exact_id(state, node, method, indexes):
            continue
        if _try_exact_slug(state, node, indexes, inference_method=method):
            continue
        if _try_exact_label(state, node, indexes, inference_method=method):
            continue
        if _try_alias(state, node, indexes, inference_method=method):
            continue
        if _try_fuzzy(state, node, settings, indexes, inference_method=method):
            continue
        _register_new(state, node, method, competition_slug, indexes)

    _finalize_evidence(state, indexes)
    return state
