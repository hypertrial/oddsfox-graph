"""Pure, versioned graph-display derivations shared by explorer surfaces."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import heapq
from typing import cast

from .._discovery.versions import ESSENTIAL_PROJECTION_VERSION
from .contracts import GraphDisplayStats, RelationshipDetail


@dataclass(frozen=True, slots=True)
class ProjectionEdge:
    """Minimal normalized edge used by the display-essential projection."""

    id: str
    source: str
    target: str
    relation: str
    confidence: float


@dataclass(frozen=True, slots=True)
class EssentialProjection:
    """Auditable result of one deterministic essential-edge projection."""

    retained_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    redundant_ids: tuple[str, ...]
    version: str = ESSENTIAL_PROJECTION_VERSION


def essential_projection(
    edges: Iterable[ProjectionEdge],
    *,
    preserve_ids: frozenset[str] = frozenset(),
) -> EssentialProjection:
    """Collapse duplicates and remove only confidence-supported implications.

    The implementation preserves the v1 behavior but shares graph analysis for
    every implication at the same confidence threshold.  Equivalence edges are
    traversable in both directions, cycle-forming implications are retained,
    and a removable implication needs an alternate path whose bottleneck
    confidence is at least the direct edge confidence.
    """

    ordered = sorted(
        edges,
        key=lambda edge: (
            edge.id not in preserve_ids,
            -edge.confidence,
            edge.id,
        ),
    )
    deduplicated: dict[tuple[str, str, str], ProjectionEdge] = {}
    duplicate_ids: list[str] = []
    for edge in ordered:
        source, target = edge.source, edge.target
        if edge.relation != "implies" and source > target:
            source, target = target, source
        key = (edge.relation, source, target)
        if key in deduplicated:
            duplicate_ids.append(edge.id)
        else:
            deduplicated[key] = edge

    candidates = tuple(deduplicated.values())
    traversable = tuple(
        edge
        for edge in candidates
        if edge.relation in {"implies", "equivalent"}
    )
    nodes = tuple(
        sorted(
            {
                endpoint
                for edge in traversable
                for endpoint in (edge.source, edge.target)
            }
        )
    )
    full_arcs = _directed_arcs(traversable)
    full_components = _strong_components(nodes, full_arcs)

    retained_ids = {
        edge.id
        for edge in candidates
        if edge.relation != "implies"
        or edge.id in preserve_ids
        or edge.source == edge.target
        or full_components.get(edge.source) == full_components.get(edge.target)
    }
    reducible = tuple(
        edge
        for edge in candidates
        if edge.relation == "implies" and edge.id not in retained_ids
    )

    for threshold in sorted({edge.confidence for edge in reducible}, reverse=True):
        eligible = tuple(edge for edge in traversable if edge.confidence >= threshold)
        arcs = _directed_arcs(eligible)
        components = _strong_components(nodes, arcs)
        component_count = len(set(components.values()))
        dag, multiplicity = _component_dag(arcs, components)
        reachability = _dag_reachability(dag, component_count)
        for edge in reducible:
            if edge.confidence != threshold:
                continue
            source_component = components[edge.source]
            target_component = components[edge.target]
            if source_component == target_component:
                retained_ids.add(edge.id)
                continue
            alternate = (
                multiplicity.get((source_component, target_component), 0) > 1
            )
            if not alternate:
                target_bit = 1 << target_component
                alternate = any(
                    neighbor != target_component
                    and ((1 << neighbor) | reachability[neighbor]) & target_bit
                    for neighbor in dag.get(source_component, ())
                )
            if not alternate:
                retained_ids.add(edge.id)

    retained = tuple(
        edge.id
        for edge in sorted(candidates, key=_display_edge_key)
        if edge.id in retained_ids
    )
    redundant = tuple(
        edge.id
        for edge in sorted(candidates, key=_display_edge_key)
        if edge.id not in retained_ids
    )
    return EssentialProjection(
        retained_ids=retained,
        duplicate_ids=tuple(sorted(duplicate_ids)),
        redundant_ids=redundant,
    )


def essential_relationship_rows(
    rows: Iterable[dict[str, object]],
    *,
    preserve_proposal_ids: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """Project raw ``logic_edges_v`` rows through the canonical reducer."""

    materialized = list(rows)
    by_id = {str(row["proposal_id"]): row for row in materialized}
    projection = essential_projection(
        (
            ProjectionEdge(
                id=str(row["proposal_id"]),
                source=str(row["src_node_id"]),
                target=str(row["dst_node_id"]),
                relation=str(row["edge_type"]),
                confidence=float(cast(float, row["confidence"])),
            )
            for row in materialized
        ),
        preserve_ids=preserve_proposal_ids,
    )
    return [by_id[edge_id] for edge_id in projection.retained_ids]


def graph_display_stats(
    labels: Sequence[str],
    edges: Sequence[tuple[str, str]],
    *,
    input_edge_count: int | None = None,
) -> GraphDisplayStats:
    """Return the canonical human-display counts and density recommendation."""

    node_count = len(labels)
    edge_count = len(edges)
    degree: dict[str, int] = defaultdict(int)
    for source, target in edges:
        degree[source] += 1
        degree[target] += 1
    density = (
        min(1.0, edge_count / (node_count * (node_count - 1)))
        if node_count > 1
        else 0.0
    )
    uniqueness = (
        len({label.casefold() for label in labels}) / node_count
        if node_count
        else 1.0
    )
    maximum_degree = max(degree.values(), default=0)
    network = (
        node_count <= 15
        and edge_count <= 24
        and density <= 0.15
        and uniqueness >= 0.50
        and maximum_degree <= 8
    )
    total_edges = edge_count if input_edge_count is None else input_edge_count
    return GraphDisplayStats(
        input_node_count=node_count,
        input_edge_count=total_edges,
        display_node_count=node_count,
        display_edge_count=edge_count,
        omitted_edge_count=max(0, total_edges - edge_count),
        density=density,
        label_uniqueness=uniqueness,
        max_degree=maximum_degree,
        recommended_representation="network" if network else "grouped",
    )


def human_highlight_ids(
    relationships: Iterable[RelationshipDetail],
    *,
    limit: int = 12,
) -> tuple[str, ...]:
    """Return the prefix-stable canonical human highlight order."""

    ordered = sorted(
        relationships,
        key=lambda item: (
            -max(item.source.stage_rank, item.target.stage_rank),
            -item.confidence,
            item.proposal_id,
        ),
    )
    selected: list[str] = []
    teams: set[str] = set()
    templates: set[tuple[object, ...]] = set()
    endpoints: set[str] = set()
    for item in ordered:
        item_teams = {
            item.source.canonical_team_name,
            item.target.canonical_team_name,
        }
        template = (
            item.relation,
            item.source.stage_rank,
            item.target.stage_rank,
            item.source.is_progression_token,
            item.target.is_progression_token,
        )
        if item_teams & teams or template in templates:
            continue
        if item.source.id in endpoints or item.target.id in endpoints:
            continue
        selected.append(item.proposal_id)
        teams.update(item_teams)
        templates.add(template)
        endpoints.update((item.source.id, item.target.id))
        if len(selected) == limit:
            break
    return tuple(selected)


def _directed_arcs(
    edges: Iterable[ProjectionEdge],
) -> tuple[tuple[str, str, str], ...]:
    arcs: list[tuple[str, str, str]] = []
    for edge in edges:
        arcs.append((edge.source, edge.target, edge.id))
        if edge.relation == "equivalent":
            arcs.append((edge.target, edge.source, edge.id))
    return tuple(arcs)


def _strong_components(
    nodes: Sequence[str],
    arcs: Sequence[tuple[str, str, str]],
) -> dict[str, int]:
    """Iterative Kosaraju decomposition safe for the 5,000-node viewer bound."""

    adjacency: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)
    for source, target, _ in arcs:
        adjacency[source].append(target)
        reverse[target].append(source)
    for values in (*adjacency.values(), *reverse.values()):
        values.sort()

    visited: set[str] = set()
    finished: list[str] = []
    for root in nodes:
        if root in visited:
            continue
        visited.add(root)
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, index = stack[-1]
            neighbors = adjacency.get(node, ())
            if index < len(neighbors):
                neighbor = neighbors[index]
                stack[-1] = (node, index + 1)
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append((neighbor, 0))
            else:
                stack.pop()
                finished.append(node)

    components: dict[str, int] = {}
    next_component = 0
    for root in reversed(finished):
        if root in components:
            continue
        component = next_component
        next_component += 1
        components[root] = component
        reverse_stack = [root]
        while reverse_stack:
            node = reverse_stack.pop()
            for neighbor in reverse.get(node, ()):
                if neighbor not in components:
                    components[neighbor] = component
                    reverse_stack.append(neighbor)
    return components


def _component_dag(
    arcs: Sequence[tuple[str, str, str]],
    components: Mapping[str, int],
) -> tuple[dict[int, tuple[int, ...]], dict[tuple[int, int], int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    multiplicity: dict[tuple[int, int], int] = defaultdict(int)
    for source, target, _ in arcs:
        left = components[source]
        right = components[target]
        if left == right:
            continue
        adjacency[left].add(right)
        multiplicity[(left, right)] += 1
    return (
        {node: tuple(sorted(neighbors)) for node, neighbors in adjacency.items()},
        dict(multiplicity),
    )


def _dag_reachability(
    adjacency: Mapping[int, Sequence[int]],
    node_count: int,
) -> tuple[int, ...]:
    indegree = [0] * node_count
    for neighbors in adjacency.values():
        for neighbor in neighbors:
            indegree[neighbor] += 1
    ready = [node for node, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for neighbor in adjacency.get(node, ()):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heapq.heappush(ready, neighbor)
    if len(order) != node_count:
        raise ValueError("Condensed essential-projection graph must be acyclic")
    reachability = [0] * node_count
    for node in reversed(order):
        mask = 0
        for neighbor in adjacency.get(node, ()):
            mask |= (1 << neighbor) | reachability[neighbor]
        reachability[node] = mask
    return tuple(reachability)


def _display_edge_key(edge: ProjectionEdge) -> tuple[object, ...]:
    return (
        -edge.confidence,
        edge.relation,
        edge.source,
        edge.target,
        edge.id,
    )
