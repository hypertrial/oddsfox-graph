"""On-demand transitive closure over IMPLIES edges."""

from __future__ import annotations

from collections import deque

import rustworkx as rx

from oddsgraph.ontology import EdgeType
from oddsgraph.schema import CanonicalEdge


def compute_implies_closure(edges: list[CanonicalEdge]) -> list[CanonicalEdge]:
    """Return transitive IMPLIES edges not already present as direct edges.

    Each synthetic edge uses ``derivation_type="transitive"`` and a shortest
    ``path`` of node ids stored in ``premises`` (source … target).
    """
    implies = [e for e in edges if e.edge_type == EdgeType.IMPLIES]
    if not implies:
        return []

    graph = rx.PyDiGraph()
    node_index: dict[str, int] = {}
    index_node: dict[int, str] = {}
    direct: set[tuple[str, str]] = set()
    evidence_by_pair: dict[tuple[str, str], list[str]] = {}

    def idx(node_id: str) -> int:
        if node_id not in node_index:
            i = graph.add_node(node_id)
            node_index[node_id] = i
            index_node[i] = node_id
        return node_index[node_id]

    for edge in implies:
        src = idx(edge.source_id)
        tgt = idx(edge.target_id)
        graph.add_edge(src, tgt, edge)
        pair = (edge.source_id, edge.target_id)
        direct.add(pair)
        evidence_by_pair[pair] = list(edge.evidence_market_ids)

    closure: list[CanonicalEdge] = []
    for source_id, source_idx in node_index.items():
        descendant_idxs = rx.descendants(graph, source_idx)
        for target_idx in descendant_idxs:
            target_id = index_node[target_idx]
            if (source_id, target_id) in direct:
                continue
            path = _shortest_path(graph, index_node, source_idx, target_idx)
            if path is None or len(path) < 3:
                continue
            # Union evidence along the path edges.
            evidence: set[str] = set()
            for a, b in zip(path, path[1:]):
                evidence.update(evidence_by_pair.get((a, b), []))
            if not evidence:
                evidence.add("closure:synthetic")
            closure.append(
                CanonicalEdge(
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=EdgeType.IMPLIES,
                    confidence=1.0,
                    evidence_market_ids=sorted(evidence),
                    evidence_text="transitive closure",
                    inference_method="closure",
                    derivation_type="transitive",
                    rule_id=None,
                    rule_version=None,
                    premises=path,
                )
            )
    return closure


def _shortest_path(
    graph: rx.PyDiGraph,
    index_node: dict[int, str],
    source_idx: int,
    target_idx: int,
) -> list[str] | None:
    """BFS shortest path of node ids from source to target."""
    if source_idx == target_idx:
        return [index_node[source_idx]]
    queue: deque[int] = deque([source_idx])
    parent: dict[int, int | None] = {source_idx: None}
    while queue:
        current = queue.popleft()
        for neighbor in graph.successor_indices(current):
            if neighbor in parent:
                continue
            parent[neighbor] = current
            if neighbor == target_idx:
                path_idx = [target_idx]
                cursor: int | None = target_idx
                while parent[cursor] is not None:
                    cursor = parent[cursor]
                    assert cursor is not None
                    path_idx.append(cursor)
                path_idx.reverse()
                return [index_node[i] for i in path_idx]
            queue.append(neighbor)
    return None
