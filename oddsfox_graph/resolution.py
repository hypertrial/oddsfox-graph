"""Entity resolution from fragment-local nodes to canonical graph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from oddsfox_graph import ids
from oddsfox_graph.config import Settings
from oddsfox_graph.ontology import NodeType
from oddsfox_graph.schema import CanonicalNode, GraphFragment, Node, UnresolvedEntity


@dataclass
class ResolutionState:
    canonical_nodes: dict[str, CanonicalNode] = field(default_factory=dict)
    unresolved: list[UnresolvedEntity] = field(default_factory=list)
    local_to_canonical: dict[str, str] = field(default_factory=dict)
    tier_counts: dict[str, int] = field(default_factory=dict)


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


def _suggested_canonical_id(node: Node) -> str:
    """Deterministic canonical ID for inferred entity types."""
    if node.type == NodeType.TEAM:
        return ids.team_id(node.label)
    if node.type == NodeType.COMPETITION:
        return ids.competition_id(node.label)
    if node.type == NodeType.STAGE:
        return ids.stage_id("world-cup-2026", node.label)
    if node.type == NodeType.GROUP:
        return ids.group_id("world-cup-2026", node.label)
    if node.type == NodeType.ROUND:
        return ids.round_id("world-cup-2026", node.label)
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


def resolve_fragments(
    fragments: list[GraphFragment],
    settings: Settings,
    inference_method: str = "unknown",
) -> ResolutionState:
    state = ResolutionState()
    all_nodes: list[tuple[Node, str]] = []

    for fragment in fragments:
        for node in fragment.nodes:
            all_nodes.append((node, inference_method))

    by_slug: dict[tuple[NodeType, str], CanonicalNode] = {}
    by_label: dict[tuple[NodeType, str], CanonicalNode] = {}
    by_alias: dict[tuple[NodeType, str], CanonicalNode] = {}

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
        best_score = 0
        best_canonical: CanonicalNode | None = None
        for existing in state.canonical_nodes.values():
            if existing.type != node.type:
                continue
            score = fuzz.token_sort_ratio(existing.label, node.label)
            if score > best_score:
                best_score = score
                best_canonical = existing
        if best_canonical and best_score >= settings.fuzzy_threshold:
            state.canonical_nodes[best_canonical.canonical_id] = _merge_canonical(
                best_canonical, node, "fuzzy"
            )
            state.local_to_canonical[node.local_id] = best_canonical.canonical_id
            _register_tier(state, "fuzzy")
            continue

        # Tier 6: unresolved review queue (no forced merge)
        if node.confidence < settings.minimum_confidence:
            unresolved_id = f"unresolved:{node.type.value}:{ids.slugify(node.label)}"
            state.unresolved.append(
                UnresolvedEntity(
                    local_id=node.local_id,
                    type=node.type,
                    label=node.label,
                    aliases=list(node.aliases),
                    confidence=node.confidence,
                    evidence_market_ids=list(node.evidence_market_ids),
                    inference_method=method,
                    reason="no_match",
                )
            )
            state.local_to_canonical[node.local_id] = unresolved_id
            _register_tier(state, "unresolved")
            continue

        # No match found but confidence sufficient: create new canonical entity
        new_id = _suggested_canonical_id(node)
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
            )
            _register_tier(state, "new_entity")

    return state
