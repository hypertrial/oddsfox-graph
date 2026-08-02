import type { ExplorerEdge } from "./types";

export const ESSENTIAL_PROJECTION_VERSION = "essential-projection-v2" as const;

interface Arc {
  source: string;
  target: string;
}

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
  const nodes = [...new Set(traversable.flatMap((edge) => [edge.source, edge.target]))]
    .sort(compareText);
  const fullComponents = stronglyConnectedComponents(nodes, directedArcs(traversable));
  const retained = new Set(candidates
    .filter((edge) => edge.relation !== "implies"
      || preserveIds.has(edge.id)
      || edge.source === edge.target
      || fullComponents.get(edge.source) === fullComponents.get(edge.target))
    .map((edge) => edge.id));
  const reducible = candidates.filter((edge) => edge.relation === "implies" && !retained.has(edge.id));
  const thresholds = [...new Set(reducible.map((edge) => edge.confidence))]
    .sort((left, right) => right - left);

  for (const threshold of thresholds) {
    const eligible = traversable.filter((edge) => edge.confidence >= threshold);
    const arcs = directedArcs(eligible);
    const components = stronglyConnectedComponents(nodes, arcs);
    const componentCount = new Set(components.values()).size;
    const { adjacency, multiplicity } = componentDag(arcs, components);
    const reachability = dagReachability(adjacency, componentCount);
    for (const edge of reducible) {
      if (edge.confidence !== threshold) continue;
      const source = components.get(edge.source)!;
      const target = components.get(edge.target)!;
      if (source === target) {
        retained.add(edge.id);
        continue;
      }
      let alternate = (multiplicity.get(`${source}:${target}`) ?? 0) > 1;
      if (!alternate) {
        alternate = [...(adjacency.get(source) ?? [])].some((neighbor) =>
          neighbor !== target && reachability[neighbor].has(target));
      }
      if (!alternate) retained.add(edge.id);
    }
  }

  return candidates
    .filter((edge) => retained.has(edge.id))
    .sort((left, right) => right.confidence - left.confidence
      || compareText(left.relation, right.relation)
      || compareText(left.source, right.source)
      || compareText(left.target, right.target)
      || compareText(left.id, right.id));
}

function directedArcs(edges: ExplorerEdge[]): Arc[] {
  return edges.flatMap((edge) => edge.relation === "equivalent"
    ? [{ source: edge.source, target: edge.target }, { source: edge.target, target: edge.source }]
    : [{ source: edge.source, target: edge.target }]);
}

function stronglyConnectedComponents(nodes: string[], arcs: Arc[]): Map<string, number> {
  const adjacency = new Map<string, string[]>();
  const reverse = new Map<string, string[]>();
  for (const arc of arcs) {
    adjacency.set(arc.source, [...(adjacency.get(arc.source) ?? []), arc.target]);
    reverse.set(arc.target, [...(reverse.get(arc.target) ?? []), arc.source]);
  }
  for (const values of [...adjacency.values(), ...reverse.values()]) values.sort(compareText);

  const visited = new Set<string>();
  const finished: string[] = [];
  for (const root of nodes) {
    if (visited.has(root)) continue;
    visited.add(root);
    const stack: Array<{ node: string; index: number }> = [{ node: root, index: 0 }];
    while (stack.length > 0) {
      const current = stack.at(-1)!;
      const neighbors = adjacency.get(current.node) ?? [];
      if (current.index < neighbors.length) {
        const neighbor = neighbors[current.index];
        current.index += 1;
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          stack.push({ node: neighbor, index: 0 });
        }
      } else {
        stack.pop();
        finished.push(current.node);
      }
    }
  }

  const components = new Map<string, number>();
  let component = 0;
  for (const root of [...finished].reverse()) {
    if (components.has(root)) continue;
    components.set(root, component);
    const stack = [root];
    while (stack.length > 0) {
      const node = stack.pop()!;
      for (const neighbor of reverse.get(node) ?? []) {
        if (!components.has(neighbor)) {
          components.set(neighbor, component);
          stack.push(neighbor);
        }
      }
    }
    component += 1;
  }
  return components;
}

function componentDag(arcs: Arc[], components: Map<string, number>) {
  const adjacency = new Map<number, Set<number>>();
  const multiplicity = new Map<string, number>();
  for (const arc of arcs) {
    const source = components.get(arc.source)!;
    const target = components.get(arc.target)!;
    if (source === target) continue;
    const neighbors = adjacency.get(source) ?? new Set<number>();
    neighbors.add(target);
    adjacency.set(source, neighbors);
    const key = `${source}:${target}`;
    multiplicity.set(key, (multiplicity.get(key) ?? 0) + 1);
  }
  return { adjacency, multiplicity };
}

function dagReachability(
  adjacency: Map<number, Set<number>>,
  nodeCount: number,
): Array<Set<number>> {
  const indegree = Array.from({ length: nodeCount }, () => 0);
  for (const neighbors of adjacency.values()) {
    for (const neighbor of neighbors) indegree[neighbor] += 1;
  }
  const ready = indegree
    .map((degree, node) => ({ degree, node }))
    .filter(({ degree }) => degree === 0)
    .map(({ node }) => node)
    .sort((left, right) => left - right);
  const order: number[] = [];
  while (ready.length > 0) {
    const node = ready.shift()!;
    order.push(node);
    for (const neighbor of [...(adjacency.get(node) ?? [])].sort((left, right) => left - right)) {
      indegree[neighbor] -= 1;
      if (indegree[neighbor] === 0) {
        ready.push(neighbor);
        ready.sort((left, right) => left - right);
      }
    }
  }
  if (order.length !== nodeCount) throw new Error("Condensed essential graph must be acyclic");
  const reachability = Array.from({ length: nodeCount }, () => new Set<number>());
  for (const node of [...order].reverse()) {
    for (const neighbor of adjacency.get(node) ?? []) {
      reachability[node].add(neighbor);
      for (const target of reachability[neighbor]) reachability[node].add(target);
    }
  }
  return reachability;
}
