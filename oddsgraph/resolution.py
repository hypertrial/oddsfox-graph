"""Entity resolution from fragment-local nodes to canonical graph nodes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from oddsgraph import ids
from oddsgraph.config import Settings
from oddsgraph.ontology import NodeType
from oddsgraph.schema import CanonicalNode, GraphFragment, Node

DEFAULT_COMPETITION_SLUG = "world-cup-2026"


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


def _merge_canonical(
    state: ResolutionState,
    existing: CanonicalNode,
    node: Node,
    method: str,
    indexes: _ResolutionIndexes,
) -> CanonicalNode:
    evidence = indexes.evidence_sets.setdefault(existing.canonical_id, set())
    evidence.update(node.evidence_market_ids)
    previous_aliases = set(existing.aliases) | {existing.label}
    merged_aliases = sorted(set(existing.aliases) | set(node.aliases) | {node.label})
    merged = existing.model_copy(
        update={
            "confidence": max(existing.confidence, node.confidence),
            # Evidence lists are finalized once at the end of resolve_fragments.
            "aliases": merged_aliases,
            "resolution_method": method,
        }
    )
    indexes.by_slug[(merged.type, ids.slugify(merged.label))] = merged
    indexes.by_label[(merged.type, ids.normalize_label(merged.label))] = merged
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
    indexes.by_slug[(node.type, ids.slugify(node.label))] = canonical
    indexes.by_label[(node.type, ids.normalize_label(node.label))] = canonical
    _index_aliases(canonical, list(node.aliases) + [node.label], indexes)
    indexes.fuzzy_index.add(node.type, node.label, canonical_id)


def _bind_existing(
    state: ResolutionState,
    existing: CanonicalNode,
    node: Node,
    method: str,
    tier: str,
    indexes: _ResolutionIndexes,
) -> None:
    _merge_canonical(state, existing, node, method, indexes)
    state.local_to_canonical[node.local_id] = existing.canonical_id
    _register_tier(state, tier)


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
        _bind_existing(
            state,
            state.canonical_nodes[polymarket_id],
            node,
            "exact_id",
            "exact_id",
            indexes,
        )
        return True
    _register_canonical(
        state, polymarket_id, node, "exact_id", method, indexes
    )
    _register_tier(state, "exact_id")
    return True


def _try_exact_slug(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
) -> bool:
    slug_key = (node.type, ids.slugify(node.label))
    existing = indexes.by_slug.get(slug_key)
    if existing is None:
        return False
    _bind_existing(state, existing, node, "exact_slug", "exact_slug", indexes)
    return True


def _try_exact_label(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
) -> bool:
    label_key = (node.type, ids.normalize_label(node.label))
    existing = indexes.by_label.get(label_key)
    if existing is None:
        return False
    _bind_existing(state, existing, node, "exact_label", "exact_label", indexes)
    return True


def _try_alias(
    state: ResolutionState,
    node: Node,
    indexes: _ResolutionIndexes,
) -> bool:
    for alias in [node.label] + list(node.aliases):
        alias_key = (node.type, ids.normalize_label(alias))
        existing = indexes.by_alias.get(alias_key)
        if existing is not None and (
            node.type != NodeType.TEAM
            or _team_alias_compatible(alias, node.label, existing.label)
        ):
            _bind_existing(state, existing, node, "alias", "alias", indexes)
            return True
        if node.type != NodeType.TEAM:
            continue
        if not _team_code_maps_to_label(alias, node.label):
            continue
        for code_alias in ids.team_aliases_from_code(alias):
            code_key = (node.type, ids.normalize_label(code_alias))
            existing = indexes.by_alias.get(code_key)
            if existing is None:
                continue
            _bind_existing(state, existing, node, "alias", "alias", indexes)
            return True
    return False


def _try_fuzzy(
    state: ResolutionState,
    node: Node,
    settings: Settings,
    indexes: _ResolutionIndexes,
) -> bool:
    best_canonical_id, _ = indexes.fuzzy_index.best_match(
        node.type,
        node.label,
        settings.fuzzy_threshold,
    )
    if best_canonical_id is None:
        return False
    _bind_existing(
        state,
        state.canonical_nodes[best_canonical_id],
        node,
        "fuzzy",
        "fuzzy",
        indexes,
    )
    return True


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
        )
        return
    _register_canonical(state, new_id, node, "new_entity", method, indexes)
    _register_tier(state, "new_entity")


def _finalize_evidence(state: ResolutionState, indexes: _ResolutionIndexes) -> None:
    for canonical_id, canonical in list(state.canonical_nodes.items()):
        state.canonical_nodes[canonical_id] = canonical.model_copy(
            update={
                "evidence_market_ids": _materialize_evidence(canonical_id, indexes)
            }
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
        if _try_exact_slug(state, node, indexes):
            continue
        if _try_exact_label(state, node, indexes):
            continue
        if _try_alias(state, node, indexes):
            continue
        if _try_fuzzy(state, node, settings, indexes):
            continue
        _register_new(state, node, method, competition_slug, indexes)

    _finalize_evidence(state, indexes)
    return state
