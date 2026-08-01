import type {
  CompareResult,
  EdgeMode,
  EntitySearchResult,
  EvidenceTier,
  ExploreHome,
  GraphMetadata,
  GraphView,
  MarketDetail,
  RecordingPlan,
  Relation,
  RelationshipDetail,
  SearchNode,
  StageDetail,
  TeamDetail,
} from "./types";
import { essentialGraphEdges } from "./graphEdges";

let staticMode = false;

async function staticSnapshot() {
  return (await import("./staticData")).loadStaticSnapshot();
}

async function staticProvider() {
  return import("./staticData");
}

async function json<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });
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
    const loaded = await staticSnapshot();
    staticMode = true;
    return loaded.metadata;
  }
}

export async function exploreHome(
  teamLimit = 24,
  highlightLimit = 6,
): Promise<ExploreHome> {
  if (staticMode) return (await staticProvider()).staticExploreHome(teamLimit, highlightLimit);
  const params = new URLSearchParams({
    team_limit: String(teamLimit),
    highlight_limit: String(highlightLimit),
  });
  return json<ExploreHome>(`/api/v1/explore?${params}`);
}

export async function stageDetail(stageKey: string): Promise<StageDetail> {
  if (staticMode) return (await staticProvider()).staticStageDetail(stageKey);
  return json<StageDetail>(`/api/v1/stages/${encodeURIComponent(stageKey)}`);
}

export async function teamDetail(teamKey: string): Promise<TeamDetail> {
  if (staticMode) return (await staticProvider()).staticTeamDetail(teamKey);
  return json<TeamDetail>(`/api/v1/teams/${encodeURIComponent(teamKey)}`);
}

export async function marketDetail(marketId: string): Promise<MarketDetail> {
  if (staticMode) return (await staticProvider()).staticMarketDetail(marketId);
  return json<MarketDetail>(`/api/v1/markets/${encodeURIComponent(marketId)}`);
}

export async function relationshipDetail(proposalId: string): Promise<RelationshipDetail> {
  if (staticMode) return (await staticProvider()).staticRelationshipDetail(proposalId);
  return json<RelationshipDetail>(`/api/v1/relationships/${encodeURIComponent(proposalId)}`);
}

export async function entitySearch(
  query: string,
  limit = 12,
): Promise<EntitySearchResult[]> {
  if (staticMode) return (await staticProvider()).staticEntitySearch(query, limit);
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return json<EntitySearchResult[]>(`/api/v1/entity-search?${params}`);
}

export async function compare(
  source: string,
  target: string,
  maxHops = 4,
): Promise<CompareResult> {
  if (staticMode) return (await staticProvider()).staticCompare(source, target, maxHops);
  const params = new URLSearchParams({
    a: source,
    b: target,
    max_hops: String(maxHops),
  });
  return json<CompareResult>(`/api/v1/compare?${params}`);
}

export async function overview(
  level: "component" | "event" | "proposition",
  relation: Relation | "all",
  minConfidence: number,
  includeCompatible: boolean,
  evidenceTier: EvidenceTier,
  signal?: AbortSignal,
  edgeMode: EdgeMode = "all",
): Promise<GraphView> {
  if (level === "proposition") {
    let base: GraphView;
    if (staticMode) {
      base = (await staticSnapshot()).view;
    } else {
      const params = graphFilterParameters(relation, minConfidence, evidenceTier, "all");
      params.set("level", "proposition");
      params.set("include_compatible", String(includeCompatible));
      base = await json<GraphView>(`/api/v1/overview?${params}`, signal);
    }
    return filterGraphView(
      base,
      relation,
      minConfidence,
      includeCompatible,
      evidenceTier,
      true,
      edgeMode,
    );
  }
  if (staticMode) return (await staticSnapshot()).view;
  const params = graphFilterParameters(relation, minConfidence, evidenceTier, edgeMode);
  params.set("level", level);
  params.set("include_compatible", String(includeCompatible));
  return json<GraphView>(`/api/v1/overview?${params}`, signal);
}

export async function recordingPlan(
  limit = 6,
  minConfidence = 0.95,
): Promise<RecordingPlan> {
  if (staticMode) {
    throw new Error("Recording requires the original graph directory served locally");
  }
  const params = new URLSearchParams({
    limit: String(limit),
    min_confidence: String(minConfidence),
  });
  return json<RecordingPlan>(`/api/v1/recording-plan?${params}`);
}

export function filterGraphView(
  view: GraphView,
  relation: Relation | "all",
  minConfidence: number,
  includeCompatible: boolean,
  evidenceTier: EvidenceTier = "all",
  progressionOnly = true,
  edgeMode: EdgeMode = "essential",
): GraphView {
  const hasProgressionSemantics = view.nodes.some(
    (node) => typeof node.progression_outcome === "boolean",
  );
  const progressionOutcomes = new Set(
    view.nodes.filter((node) => node.progression_outcome).map((node) => node.id),
  );
  const edges = view.edges.filter((edge) => {
    if (edge.confidence < minConfidence) return false;
    if (evidenceTier !== "all" && edge.evidence_tier !== evidenceTier) return false;
    if (relation !== "all") {
      if (edge.relation !== relation) return false;
      if (relation === "implies" && progressionOnly && hasProgressionSemantics) {
        return progressionOutcomes.has(edge.source) && progressionOutcomes.has(edge.target);
      }
      return true;
    }
    return includeCompatible || edge.relation !== "compatible";
  });
  const displayEdges = edgeMode === "essential" ? essentialGraphEdges(edges) : edges;
  const nodes = relation === "all" && evidenceTier === "all"
    ? view.nodes
    : filteredGraphNodes(view.nodes, displayEdges);
  return {
    ...view,
    nodes,
    edges: displayEdges,
    edge_mode: edgeMode,
    display_stats: null,
  };
}

function filteredGraphNodes(
  nodes: GraphView["nodes"],
  edges: GraphView["edges"],
): GraphView["nodes"] {
  const visible = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  const representedTeams = new Set(
    nodes
      .filter((node) => visible.has(node.id) && node.domain)
      .map((node) => node.domain),
  );
  const representatives = new Set<string>();
  const candidatesByTeam = new Map<string, GraphView["nodes"]>();
  for (const node of nodes) {
    if (!node.domain || representedTeams.has(node.domain)) continue;
    const candidates = candidatesByTeam.get(node.domain) ?? [];
    candidates.push(node);
    candidatesByTeam.set(node.domain, candidates);
  }
  for (const candidates of candidatesByTeam.values()) {
    candidates.sort((left, right) =>
      Number(right.progression_outcome === true) - Number(left.progression_outcome === true)
      || (right.progression_level ?? Number.NEGATIVE_INFINITY)
        - (left.progression_level ?? Number.NEGATIVE_INFINITY)
      || (right.market_close_epoch ?? Number.NEGATIVE_INFINITY)
        - (left.market_close_epoch ?? Number.NEGATIVE_INFINITY)
      || right.x - left.x
      || left.id.localeCompare(right.id));
    if (candidates[0]) representatives.add(candidates[0].id);
  }
  return nodes.filter((node) => visible.has(node.id) || representatives.has(node.id));
}

export async function neighborhood(node: string, hops = 2): Promise<GraphView> {
  if (staticMode) return focusedView((await staticSnapshot()).view, node, hops);
  const params = new URLSearchParams({ node, hops: String(hops), edge_mode: "all" });
  return json<GraphView>(`/api/v1/subgraph?${params}`);
}

export async function search(query: string): Promise<SearchNode[]> {
  if (staticMode) return (await staticProvider()).staticSearch(query);
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

export async function eventGraph(
  event: string,
  relation: Relation | "all",
  minConfidence: number,
  evidenceTier: EvidenceTier,
): Promise<GraphView> {
  if (staticMode) return (await staticSnapshot()).view;
  const params = graphFilterParameters(relation, minConfidence, evidenceTier, "essential");
  return json<GraphView>(`/api/v1/event-graph/${encodeURIComponent(event)}?${params}`);
}

export async function componentGraph(
  component: string,
  relation: Relation | "all",
  minConfidence: number,
  evidenceTier: EvidenceTier,
): Promise<GraphView> {
  if (staticMode) return (await staticSnapshot()).view;
  const params = graphFilterParameters(relation, minConfidence, evidenceTier, "essential");
  return json<GraphView>(`/api/v1/component-graph/${encodeURIComponent(component)}?${params}`);
}

function graphFilterParameters(
  relation: Relation | "all",
  minConfidence: number,
  evidenceTier: EvidenceTier,
  edgeMode: EdgeMode,
): URLSearchParams {
  const params = new URLSearchParams({
    min_confidence: String(minConfidence),
    include_compatible: String(relation === "compatible"),
    edge_mode: edgeMode,
  });
  if (relation !== "all") params.append("relations", relation);
  if (evidenceTier !== "all") params.append("evidence_tiers", evidenceTier);
  return params;
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
  if (staticMode) throw new Error("Proof tools are unavailable in a static snapshot");
  const params = new URLSearchParams({ from_node: from, to_node: to });
  return json<Record<string, unknown>[]>(`/api/v1/prove?${params}`);
}

export async function whyNot(
  a: string,
  b: string,
  relation: Relation,
): Promise<Record<string, unknown>> {
  if (staticMode) throw new Error("Why-not diagnostics are unavailable in a static snapshot");
  const params = new URLSearchParams({ a, b, relation });
  return json<Record<string, unknown>>(`/api/v1/why-not?${params}`);
}

function focusedView(view: GraphView, nodeId: string, hops: number): GraphView {
  const included = new Set([nodeId]);
  let frontier = new Set([nodeId]);
  for (let depth = 0; depth < hops; depth += 1) {
    const next = new Set<string>();
    for (const edge of view.edges) {
      if (frontier.has(edge.source)) next.add(edge.target);
      if (frontier.has(edge.target)) next.add(edge.source);
    }
    frontier = new Set([...next].filter((id) => !included.has(id)));
    frontier.forEach((id) => included.add(id));
  }
  return {
    ...view,
    nodes: view.nodes.filter((node) => included.has(node.id)),
    edges: view.edges.filter((edge) => included.has(edge.source) && included.has(edge.target)),
  };
}
