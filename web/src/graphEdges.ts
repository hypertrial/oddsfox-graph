import type { ExplorerEdge } from "./types";

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export function essentialGraphEdges(
  edges: ExplorerEdge[],
  preserveIds: ReadonlySet<string> = new Set(),
): ExplorerEdge[] {
  const ordered = [...edges].sort((left, right) =>
    Number(!preserveIds.has(left.id)) - Number(!preserveIds.has(right.id))
    || right.confidence - left.confidence
    || compareText(left.id, right.id));
  const deduplicated = new Map<string, ExplorerEdge>();
  for (const edge of ordered) {
    const [source, target] = edge.relation === "implies" || edge.source < edge.target
      ? [edge.source, edge.target]
      : [edge.target, edge.source];
    const key = JSON.stringify([edge.relation, source, target]);
    if (!deduplicated.has(key)) deduplicated.set(key, edge);
  }
  const candidates = [...deduplicated.values()];
  const traversable = candidates.filter((edge) =>
    edge.relation === "implies" || edge.relation === "equivalent");

  function reachable(
    source: string,
    target: string,
    excluded: string,
    minimumConfidence: number,
  ): boolean {
    const adjacency = new Map<string, string[]>();
    for (const edge of traversable) {
      if (edge.id === excluded || edge.confidence < minimumConfidence) continue;
      adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
      if (edge.relation === "equivalent") {
        adjacency.set(edge.target, [...(adjacency.get(edge.target) ?? []), edge.source]);
      }
    }
    const frontier = [source];
    const seen = new Set(frontier);
    while (frontier.length > 0) {
      const node = frontier.pop()!;
      for (const neighbor of adjacency.get(node) ?? []) {
        if (neighbor === target) return true;
        if (!seen.has(neighbor)) {
          seen.add(neighbor);
          frontier.push(neighbor);
        }
      }
    }
    return false;
  }

  return candidates
    .filter((edge) => {
      if (edge.relation !== "implies" || preserveIds.has(edge.id)) return true;
      const inCycle = reachable(edge.target, edge.source, edge.id, 0);
      const redundant = reachable(edge.source, edge.target, edge.id, edge.confidence);
      return inCycle || !redundant;
    })
    .sort((left, right) =>
      right.confidence - left.confidence
      || compareText(left.relation, right.relation)
      || compareText(left.source, right.source)
      || compareText(left.target, right.target)
      || compareText(left.id, right.id));
}
