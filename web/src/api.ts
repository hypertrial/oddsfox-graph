import type { GraphMetadata, GraphView, Relation, SearchNode } from "./types";

let staticMode = false;

async function staticSnapshot() {
  return (await import("./staticData")).loadStaticSnapshot();
}

async function searchStatic(query: string) {
  return (await import("./staticData")).staticSearch(query);
}

async function json<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string }
      | null;
    throw new Error(payload?.detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function metadata(): Promise<GraphMetadata> {
  try {
    return await json<GraphMetadata>("/api/v1/meta");
  } catch {
    staticMode = true;
    return (await staticSnapshot()).metadata;
  }
}

export async function overview(
  level: "component" | "event",
  relation: Relation | "all",
  minConfidence: number,
  includeCompatible: boolean,
): Promise<GraphView> {
  if (staticMode) {
    return filterGraphView(
      (await staticSnapshot()).view,
      relation,
      minConfidence,
      includeCompatible,
    );
  }
  const params = new URLSearchParams({
    level,
    min_confidence: String(minConfidence),
    include_compatible: String(includeCompatible),
  });
  if (relation !== "all") params.append("relations", relation);
  return json<GraphView>(`/api/v1/overview?${params}`);
}

export function filterGraphView(
  view: GraphView,
  relation: Relation | "all",
  minConfidence: number,
  includeCompatible: boolean,
): GraphView {
  const edges = view.edges.filter((edge) => {
    if (edge.confidence < minConfidence) return false;
    if (relation !== "all") return edge.relation === relation;
    return includeCompatible || edge.relation !== "compatible";
  });
  return { ...view, edges };
}

export async function neighborhood(node: string, hops = 2): Promise<GraphView> {
  if (staticMode) return (await staticSnapshot()).view;
  const params = new URLSearchParams({ node, hops: String(hops) });
  return json<GraphView>(`/api/v1/subgraph?${params}`);
}

export async function search(query: string): Promise<SearchNode[]> {
  if (staticMode) return searchStatic(query);
  return json<SearchNode[]>(`/api/v1/search?q=${encodeURIComponent(query)}&limit=12`);
}

export async function nodeDetail(node: string): Promise<Record<string, unknown>> {
  if (staticMode) {
    const selected = (await staticSnapshot()).view.nodes.find((item) => item.id === node);
    return { node: selected ?? null, static: true };
  }
  return json<Record<string, unknown>>(`/api/v1/nodes/${encodeURIComponent(node)}`);
}

export async function eventDetail(event: string): Promise<Record<string, unknown>> {
  if (staticMode) return { event, static: true };
  return json<Record<string, unknown>>(`/api/v1/events/${encodeURIComponent(event)}`);
}

export async function componentDetail(component: string): Promise<Record<string, unknown>> {
  if (staticMode) return { component, static: true };
  return json<Record<string, unknown>>(`/api/v1/components/${encodeURIComponent(component)}`);
}

export async function edgeDetail(proposal: string): Promise<Record<string, unknown>> {
  if (staticMode) {
    const selected = (await staticSnapshot()).view.edges.find((item) => item.id === proposal);
    return { edge: selected ?? null, static: true };
  }
  return json<Record<string, unknown>>(`/api/v1/edges/${encodeURIComponent(proposal)}`);
}

export async function prove(from: string, to: string): Promise<Record<string, unknown>[]> {
  if (staticMode) return [];
  const params = new URLSearchParams({ from_node: from, to_node: to });
  return json<Record<string, unknown>[]>(`/api/v1/prove?${params}`);
}

export async function whyNot(
  a: string,
  b: string,
  relation: Relation,
): Promise<Record<string, unknown>> {
  if (staticMode) return { status: "static_snapshot", a, b, relation };
  const params = new URLSearchParams({ a, b, relation });
  return json<Record<string, unknown>>(`/api/v1/why-not?${params}`);
}
