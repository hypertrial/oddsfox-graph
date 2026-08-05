"""Entity resolution from fragment-local nodes to canonical graph nodes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from oddsfox_graph import ids
from oddsfox_graph.config import Settings
from oddsfox_graph.ontology import NodeType
from oddsfox_graph.schema import CanonicalNode, GraphFragment, Node

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


def _merge_canonical(existing: CanonicalNode, node: Node, method: str) -> CanonicalNode:
    merged_evidence = sorted(
        set(existing.evidence_market_ids) | set(node.evidence_market_ids)
    )
    merged_aliases = sorted(set(existing.aliases) | set(node.aliases) | {node.label})
    return existing.model_copy(
        update={
            "confidence": max(existing.confidence, node.confidence),
            "evidence_market_ids": merged_evidence,
            "aliases": merged_aliases,
            "resolution_method": method,
        }
    )


def _register_canonical(
    state: ResolutionState,
    canonical_id: str,
    node: Node,
    method: str,
    inference_method: str,
    by_slug: dict[tuple[NodeType, str], CanonicalNode],
    by_label: dict[tuple[NodeType, str], CanonicalNode],
    by_alias: dict[tuple[NodeType, str], CanonicalNode],
    fuzzy_index: _FuzzyIndex,
) -> None:
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
    by_slug[(node.type, ids.slugify(node.label))] = canonical
    by_label[(node.type, ids.normalize_label(node.label))] = canonical
    for alias in node.aliases:
        by_alias[(node.type, ids.normalize_label(alias))] = canonical
    for alias in [node.label]:
        by_alias[(node.type, ids.normalize_label(alias))] = canonical
    fuzzy_index.add(node.type, node.label, canonical_id)


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
            all_nodes.append((node, method))

    competition_slug = _competition_slug_from_fragments(fragments)
    by_slug: dict[tuple[NodeType, str], CanonicalNode] = {}
    by_label: dict[tuple[NodeType, str], CanonicalNode] = {}
    by_alias: dict[tuple[NodeType, str], CanonicalNode] = {}
    fuzzy_index = _FuzzyIndex()

    for node, method in all_nodes:
        polymarket_id = _polymarket_canonical_id(node)

        # Tier 1: exact Polymarket identifier
        if polymarket_id:
            if polymarket_id in state.canonical_nodes:
                existing = state.canonical_nodes[polymarket_id]
                state.canonical_nodes[polymarket_id] = _merge_canonical(
                    existing, node, "exact_id"
                )
                state.local_to_canonical[node.local_id] = polymarket_id
                _register_tier(state, "exact_id")
                continue
            _register_canonical(
                state,
                polymarket_id,
                node,
                "exact_id",
                method,
                by_slug,
                by_label,
                by_alias,
                fuzzy_index,
            )
            _register_tier(state, "exact_id")
            continue

        # Tier 2: exact normalized slug
        slug_key = (node.type, ids.slugify(node.label))
        if slug_key in by_slug:
            existing = by_slug[slug_key]
            state.canonical_nodes[existing.canonical_id] = _merge_canonical(
                existing, node, "exact_slug"
            )
            state.local_to_canonical[node.local_id] = existing.canonical_id
            _register_tier(state, "exact_slug")
            continue

        # Tier 3: exact normalized label
        label_key = (node.type, ids.normalize_label(node.label))
        if label_key in by_label:
            existing = by_label[label_key]
            state.canonical_nodes[existing.canonical_id] = _merge_canonical(
                existing, node, "exact_label"
            )
            state.local_to_canonical[node.local_id] = existing.canonical_id
            _register_tier(state, "exact_label")
            continue

        # Tier 4: alias match
        alias_matched = False
        for alias in [node.label] + list(node.aliases):
            alias_key = (node.type, ids.normalize_label(alias))
            if alias_key in by_alias:
                existing = by_alias[alias_key]
                state.canonical_nodes[existing.canonical_id] = _merge_canonical(
                    existing, node, "alias"
                )
                state.local_to_canonical[node.local_id] = existing.canonical_id
                _register_tier(state, "alias")
                alias_matched = True
                break
            if node.type == NodeType.TEAM:
                for code_alias in ids.team_aliases_from_code(alias):
                    code_key = (node.type, ids.normalize_label(code_alias))
                    if code_key in by_alias:
                        existing = by_alias[code_key]
                        state.canonical_nodes[existing.canonical_id] = _merge_canonical(
                            existing, node, "alias"
                        )
                        state.local_to_canonical[node.local_id] = existing.canonical_id
                        _register_tier(state, "alias")
                        alias_matched = True
                        break
            if alias_matched:
                break
        if alias_matched:
            continue

        # Tier 5: conservative rapidfuzz match
        best_canonical_id, best_score = fuzzy_index.best_match(
            node.type,
            node.label,
            settings.fuzzy_threshold,
        )
        if best_canonical_id is not None:
            best_canonical = state.canonical_nodes[best_canonical_id]
            state.canonical_nodes[best_canonical_id] = _merge_canonical(
                best_canonical, node, "fuzzy"
            )
            state.local_to_canonical[node.local_id] = best_canonical_id
            _register_tier(state, "fuzzy")
            continue

        # No match found: create new canonical entity with deterministic ID
        new_id = _suggested_canonical_id(node, competition_slug)
        if new_id in state.canonical_nodes:
            existing = state.canonical_nodes[new_id]
            state.canonical_nodes[new_id] = _merge_canonical(existing, node, "new_entity")
            state.local_to_canonical[node.local_id] = new_id
            _register_tier(state, "new_entity")
        else:
            _register_canonical(
                state,
                new_id,
                node,
                "new_entity",
                method,
                by_slug,
                by_label,
                by_alias,
                fuzzy_index,
            )
            _register_tier(state, "new_entity")

    return state
