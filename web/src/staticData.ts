import * as duckdb from "@duckdb/duckdb-wasm";
import duckdbMvp from "@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url";
import mvpWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url";
import { marketsForProgressionStage, relationshipSentence } from "./human";
import { closeTimeColumns } from "./layout";
import { essentialGraphEdges } from "./graphEdges";
import type {
  ClaimSummary,
  CompareResult,
  EntitySearchResult,
  ExplorerCapabilities,
  ExplorerEdge,
  ExplorerNode,
  ExploreHome,
  GraphDisplayStats,
  GraphMetadata,
  GraphView,
  HumanHighlight,
  MarketDetail,
  RelationshipDetail,
  RelationshipGroupSummary,
  SearchNode,
  StageDetail,
  StageSummary,
  TeamDetail,
  TeamSummary,
  TournamentScope,
} from "./types";

const snapshotFiles = [
  "snapshot_nodes.parquet",
  "snapshot_edges.parquet",
  "snapshot_stages.parquet",
  "snapshot_teams.parquet",
  "snapshot_markets.parquet",
  "snapshot_claims.parquet",
  "snapshot_relationships.parquet",
  "snapshot_relationship_groups.parquet",
] as const;

interface StaticManifest {
  schema_version: string;
  package_version: string;
  source_graph: string;
  graph_content_fingerprint: string;
  build_mode: string;
  validation_status: string;
  coverage: Record<string, unknown>;
  capabilities: ExplorerCapabilities;
  tournament_scope: TournamentScope;
  display_stats: GraphDisplayStats | null;
  snapshot_files: Record<string, string>;
}

type MarketRow = Omit<MarketDetail, "claims">;

interface RelationshipRow {
  proposal_id: string;
  source_id: string;
  target_id: string;
  relation: RelationshipDetail["relation"];
  basis: string;
  confidence: number;
  evidence_tier: RelationshipDetail["evidence_tier"];
  discovery_method: string;
  explanation: string;
}

export interface StaticSnapshot {
  view: GraphView;
  metadata: GraphMetadata;
  scope: TournamentScope;
  capabilities: ExplorerCapabilities;
  displayStats: GraphDisplayStats;
  stages: StageSummary[];
  teams: TeamSummary[];
  markets: MarketDetail[];
  claims: ClaimSummary[];
  relationships: RelationshipDetail[];
  essentialRelationships: RelationshipDetail[];
  groups: RelationshipGroupSummary[];
}

let snapshot: Promise<StaticSnapshot> | null = null;

export function loadStaticSnapshot(): Promise<StaticSnapshot> {
  snapshot ??= load();
  return snapshot;
}

export async function staticExploreHome(
  teamLimit = 24,
  highlightLimit = 6,
): Promise<ExploreHome> {
  const loaded = await loadStaticSnapshot();
  const notable_relationships = selectHumanHighlights(
    loaded.essentialRelationships,
    highlightLimit,
  );
  return {
    scope: loaded.scope,
    stages: loaded.stages,
    teams: loaded.teams.slice(0, teamLimit),
    notable_relationships,
    relationship_groups: loaded.groups,
    capabilities: loaded.capabilities,
    display_stats: loaded.displayStats,
    coverage: loaded.metadata.coverage,
  };
}

export async function staticStageDetail(stageKey: string): Promise<StageDetail> {
  const loaded = await loadStaticSnapshot();
  const summary = loaded.stages.find((item) => item.stage_key === stageKey);
  if (!summary) throw new Error(`Stage ${stageKey} is not in this snapshot`);
  const markets = marketsForProgressionStage(
    loaded.markets,
    summary.normalized_progression_level,
  );
  const teamNames = new Set(markets.map((market) => market.canonical_team_name));
  return {
    summary,
    teams: loaded.teams.filter((team) => teamNames.has(team.canonical_team_name)),
    markets,
  };
}

export async function staticTeamDetail(teamKey: string): Promise<TeamDetail> {
  const loaded = await loadStaticSnapshot();
  const summary = loaded.teams.find((team) => team.team_key === teamKey);
  if (!summary) throw new Error(`Team ${teamKey} is not in this snapshot`);
  return {
    summary,
    markets: loaded.markets.filter((market) => market.canonical_team_name === summary.canonical_team_name),
  };
}

export async function staticMarketDetail(marketId: string): Promise<MarketDetail> {
  const market = (await loadStaticSnapshot()).markets.find((item) => item.market_id === marketId);
  if (!market) throw new Error(`Market ${marketId} is not in this snapshot`);
  return market;
}

export async function staticRelationshipDetail(proposalId: string): Promise<RelationshipDetail> {
  const relationship = (await loadStaticSnapshot()).relationships.find((item) => item.proposal_id === proposalId);
  if (!relationship) throw new Error(`Relationship ${proposalId} is not in this snapshot`);
  return relationship;
}

export async function staticEntitySearch(
  query: string,
  limit = 12,
): Promise<EntitySearchResult[]> {
  const loaded = await loadStaticSnapshot();
  const lowered = query.trim().toLocaleLowerCase();
  if (!lowered) return [];
  const candidates: Array<{ position: number; result: EntitySearchResult }> = [];
  const add = (kind: EntitySearchResult["kind"], id: string, label: string, description: string) => {
    const position = `${label} ${description}`.toLocaleLowerCase().indexOf(lowered);
    if (position >= 0) candidates.push({ position, result: { kind, id, label, description } });
  };
  for (const team of loaded.teams) add("team", team.team_key, team.canonical_team_name, `${team.market_count} progression markets`);
  for (const stage of loaded.stages) add("stage", stage.stage_key, stage.label, `${stage.team_count} teams`);
  for (const market of loaded.markets) add("market", market.market_id, market.question, `${market.canonical_team_name} · ${market.stage_key.replaceAll("_", " ")}`);
  for (const claim of loaded.claims) add("claim", claim.id, claim.plain_claim, `${claim.answer} outcome`);
  return candidates
    .sort((left, right) => left.position - right.position || left.result.label.localeCompare(right.result.label) || left.result.id.localeCompare(right.result.id))
    .slice(0, limit)
    .map(({ result }) => result);
}

export async function staticCompare(
  sourceId: string,
  targetId: string,
  maxHops = 4,
): Promise<CompareResult> {
  const loaded = await loadStaticSnapshot();
  const source = loaded.claims.find((claim) => claim.id === sourceId);
  const target = loaded.claims.find((claim) => claim.id === targetId);
  if (!source || !target) throw new Error("Both comparison outcomes must exist in this snapshot");
  if (sourceId === targetId) {
    return { status: "same_claim", source, target, direct: null, path: [], explanation: "You selected the same outcome twice." };
  }
  const direct = directRelationship(loaded.relationships, sourceId, targetId);
  if (direct) {
    return { status: "direct", source, target, direct, path: [], explanation: relationshipSentence(direct) };
  }
  const path = shortestPath(
    loaded.essentialRelationships,
    sourceId,
    targetId,
    maxHops,
  );
  if (path.length > 0) {
    return { status: "path", source, target, direct: null, path, explanation: `A ${path.length}-step logic path connects these outcomes.` };
  }
  return {
    status: "no_proven_relationship",
    source,
    target,
    direct: null,
    path: [],
    explanation: "No supported direct relationship or short progression proof connects these outcomes.",
  };
}

export async function staticSearch(query: string): Promise<SearchNode[]> {
  const loaded = await loadStaticSnapshot();
  const lowered = query.toLocaleLowerCase();
  const claimById = new Map(loaded.claims.map((claim) => [claim.id, claim]));
  const seenClaims = new Set<string>();
  return loaded.view.nodes
    .filter((node) => {
      const claim = claimById.get(node.id);
      return `${claim?.plain_claim ?? ""} ${claim?.question ?? ""} ${node.label}`.toLocaleLowerCase().includes(lowered)
        || node.id.toLocaleLowerCase() === lowered;
    })
    .sort((left, right) => {
      const leftClaim = claimById.get(left.id);
      const rightClaim = claimById.get(right.id);
      return Number(rightClaim?.is_progression_token ?? false) - Number(leftClaim?.is_progression_token ?? false)
        || (rightClaim?.normalized_progression_level ?? -1) - (leftClaim?.normalized_progression_level ?? -1)
        || (leftClaim?.plain_claim ?? left.label).localeCompare(rightClaim?.plain_claim ?? right.label)
        || left.id.localeCompare(right.id);
    })
    .filter((node) => {
      const label = claimById.get(node.id)?.plain_claim ?? node.label;
      if (seenClaims.has(label)) return false;
      seenClaims.add(label);
      return true;
    })
    .slice(0, 12)
    .map((node) => {
      const claim = claimById.get(node.id);
      return {
        node_id: node.id,
        market_id: node.market_id ?? "",
        outcome_label: claim?.answer ?? "",
        event_slug: node.parent_id ?? "",
        canonical_proposition: claim?.plain_claim ?? node.label,
      };
    });
}

async function load(): Promise<StaticSnapshot> {
  const manifestResponse = await fetch("./static_manifest.json");
  if (!manifestResponse.ok) throw new Error("Static explorer manifest is unavailable");
  const manifest = (await manifestResponse.json()) as StaticManifest;
  if (manifest.schema_version !== "static-explorer-v4") {
    throw new Error(`Unsupported static explorer schema ${manifest.schema_version}. Regenerate this snapshot with oddsfox-graph 0.12.0.`);
  }
  for (const file of snapshotFiles) {
    if (!manifest.snapshot_files[file]) throw new Error(`Static explorer manifest is missing ${file}`);
  }
  const worker = new Worker(mvpWorker);
  const database = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await database.instantiate(duckdbMvp);
  for (const file of snapshotFiles) await register(database, file);
  const connection = await database.connect();
  try {
    const nodes = await readRows<ExplorerNode>(connection, "snapshot_nodes", "id");
    const stages = await readRows<StageSummary>(connection, "snapshot_stages", "stage_rank, stage_key");
    const teams = await readRows<TeamSummary>(connection, "snapshot_teams", "team_key");
    const marketRows = await readRows<MarketRow>(connection, "snapshot_markets", "stage_rank, market_id");
    const claims = await readRows<ClaimSummary>(connection, "snapshot_claims", "id");
    const relationshipRows = await readRows<RelationshipRow>(connection, "snapshot_relationships", "proposal_id");
    const groups = await readRows<RelationshipGroupSummary>(connection, "snapshot_relationship_groups", "id");
    const claimById = new Map(claims.map((claim) => [claim.id, claim]));
    const markets = marketRows.map((market) => ({
      ...market,
      claims: claims
        .filter((claim) => claim.market_id === market.market_id)
        .sort((left, right) => Number(right.is_progression_token) - Number(left.is_progression_token) || left.id.localeCompare(right.id)),
    }));
    const relationships = relationshipRows.map((row) => {
      const source = claimById.get(row.source_id);
      const target = claimById.get(row.target_id);
      if (!source || !target) throw new Error(`Static relationship ${row.proposal_id} references a missing claim`);
      return {
        proposal_id: row.proposal_id,
        source,
        target,
        relation: row.relation,
        basis: row.basis,
        confidence: row.confidence,
        evidence_tier: row.evidence_tier,
        discovery_method: row.discovery_method,
        explanation: row.explanation,
      };
    });
    const teamRows = new Map(
      [...new Set(claims.map((claim) => claim.canonical_team_name))]
        .sort((left, right) => left.localeCompare(right))
        .map((team, index) => [team, index]),
    );
    const closeEpochs = claims
      .map((claim) => claim.market_close_epoch)
      .filter((epoch): epoch is number => epoch !== null);
    const hasCloseTimes = claims.length > 0 && closeEpochs.length === claims.length;
    const closeColumns = closeTimeColumns(closeEpochs);
    const marketOffsets = hasCloseTimes ? marketCloseOffsets(claims) : new Map<string, number>();
    const humanNodes = nodes.map((node) => {
      const claim = claimById.get(node.id);
      return claim ? {
        ...node,
        label: claim.plain_claim,
        domain: claim.canonical_team_name,
        progression_outcome: claim.is_progression_token,
        market_close_epoch: claim.market_close_epoch,
        x: hasCloseTimes
          ? (closeColumns.get(claim.market_close_epoch!) ?? 0) * 260
          : node.x,
        y: hasCloseTimes
          ? (teamRows.get(claim.canonical_team_name) ?? 0) * 90
            + (marketOffsets.get(claim.market_id) ?? 0) * 18
            + (claim.is_progression_token ? 0 : 8)
          : node.y,
      } : node;
    });
    const allEdges: ExplorerEdge[] = relationships.map((relationship) => ({
      id: relationship.proposal_id,
      source: relationship.source.id,
      target: relationship.target.id,
      relation: relationship.relation,
      count: 1,
      confidence: relationship.confidence,
      discovery_method: relationship.discovery_method,
      evidence_tier: relationship.evidence_tier,
      aggregation_only: false,
    }));
    const graphEdges = allEdges.filter((edge) => edge.relation !== "compatible");
    const essentialEdges = essentialGraphEdges(graphEdges);
    const essentialRelationships = relationshipsFromEdges(
      relationships,
      essentialEdges,
    );
    const displayStats = manifest.display_stats
      ?? calculateDisplayStats(humanNodes, essentialEdges, allEdges.length);
    const view: GraphView = {
      level: humanNodes[0]?.level ?? "proposition",
      nodes: humanNodes,
      edges: graphEdges,
      truncated_nodes: false,
      truncated_edges: false,
      coverage: manifest.coverage,
      edge_mode: "all",
      layout_mode: hasCloseTimes ? "close_time" : "hierarchical",
      display_stats: displayStats,
    };
    return {
      view,
      scope: manifest.tournament_scope,
      capabilities: manifest.capabilities,
      displayStats,
      stages,
      teams,
      markets,
      claims,
      relationships,
      essentialRelationships,
      groups,
      metadata: {
        package_version: manifest.package_version,
        viewer: {
          static: true,
          source_graph: manifest.source_graph,
          graph_content_fingerprint: manifest.graph_content_fingerprint,
          build_mode: manifest.build_mode,
          validation_status: manifest.validation_status,
          capabilities: manifest.capabilities,
          scope: manifest.tournament_scope,
        },
        coverage: manifest.coverage,
        build: { static: true, build_mode: manifest.build_mode, validation_status: manifest.validation_status },
      },
    };
  } finally {
    await connection.close();
    await database.terminate();
  }
}

export function marketCloseOffsets(claims: ClaimSummary[]): Map<string, number> {
  const groups = new Map<string, Set<string>>();
  for (const claim of claims) {
    const key = `${claim.canonical_team_name}\u0000${claim.market_close_epoch}`;
    const markets = groups.get(key) ?? new Set<string>();
    markets.add(claim.market_id);
    groups.set(key, markets);
  }
  const offsets = new Map<string, number>();
  for (const markets of groups.values()) {
    const ordered = [...markets].sort();
    ordered.forEach((marketId, index) => offsets.set(marketId, index - (ordered.length - 1) / 2));
  }
  return offsets;
}

async function register(database: duckdb.AsyncDuckDB, name: string) {
  await database.registerFileURL(
    name,
    new URL(`./${name}`, window.location.href).href,
    duckdb.DuckDBDataProtocol.HTTP,
    false,
  );
}

async function readRows<T>(
  connection: duckdb.AsyncDuckDBConnection,
  table: string,
  orderBy: string,
): Promise<T[]> {
  const result = await connection.query(`SELECT * FROM read_parquet('${table}.parquet') ORDER BY ${orderBy}`);
  return result.toArray().map((row) => normalize(row.toJSON()) as T);
}

function directRelationship(
  relationships: RelationshipDetail[],
  sourceId: string,
  targetId: string,
): RelationshipDetail | null {
  return relationships
    .filter((item) => (
      item.source.id === sourceId && item.target.id === targetId
    ) || (
      item.relation !== "implies" && item.source.id === targetId && item.target.id === sourceId
    ))
    .sort((left, right) => right.confidence - left.confidence || left.proposal_id.localeCompare(right.proposal_id))[0] ?? null;
}

function selectHumanHighlights(
  relationships: RelationshipDetail[],
  limit: number,
): HumanHighlight[] {
  const ordered = [...relationships].sort((left, right) =>
    Math.max(right.source.stage_rank, right.target.stage_rank)
      - Math.max(left.source.stage_rank, left.target.stage_rank)
    || right.confidence - left.confidence
    || left.proposal_id.localeCompare(right.proposal_id));
  const teams = new Set<string>();
  const templates = new Set<string>();
  const endpoints = new Set<string>();
  const selected: RelationshipDetail[] = [];
  for (const item of ordered) {
    const itemTeams = [item.source.canonical_team_name, item.target.canonical_team_name];
    const template = [
      item.relation,
      item.source.stage_rank,
      item.target.stage_rank,
      item.source.is_progression_token,
      item.target.is_progression_token,
    ].join(":");
    if (itemTeams.some((team) => teams.has(team)) || templates.has(template)) continue;
    if (endpoints.has(item.source.id) || endpoints.has(item.target.id)) continue;
    selected.push(item);
    itemTeams.forEach((team) => teams.add(team));
    templates.add(template);
    endpoints.add(item.source.id);
    endpoints.add(item.target.id);
    if (selected.length === limit) break;
  }
  return selected.map((relationship, index) => ({ rank: index + 1, relationship }));
}

function shortestPath(
  relationships: RelationshipDetail[],
  sourceId: string,
  targetId: string,
  maxHops: number,
): RelationshipDetail[] {
  const eligible = relationships.filter((item) => item.relation === "implies" || item.relation === "equivalent");
  const queue: Array<{ id: string; path: RelationshipDetail[] }> = [{ id: sourceId, path: [] }];
  const visited = new Set([sourceId]);
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current.path.length >= maxHops) continue;
    for (const edge of eligible) {
      const next = edge.source.id === current.id
        ? edge.target.id
        : edge.relation === "equivalent" && edge.target.id === current.id
          ? edge.source.id
          : null;
      if (!next || visited.has(next)) continue;
      const path = [...current.path, edge];
      if (next === targetId) return path;
      visited.add(next);
      queue.push({ id: next, path });
    }
  }
  return [];
}

function relationshipsFromEdges(
  relationships: RelationshipDetail[],
  edges: ExplorerEdge[],
): RelationshipDetail[] {
  const byId = new Map(relationships.map((relationship) => [
    relationship.proposal_id,
    relationship,
  ]));
  return edges.map((edge) => byId.get(edge.id)!).filter(Boolean);
}

function calculateDisplayStats(
  nodes: ExplorerNode[],
  edges: ExplorerEdge[],
  inputEdgeCount = edges.length,
): GraphDisplayStats {
  const degree = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const density = nodes.length > 1 ? Math.min(1, edges.length / (nodes.length * (nodes.length - 1))) : 0;
  const labelUniqueness = nodes.length ? new Set(nodes.map((node) => node.label.toLocaleLowerCase())).size / nodes.length : 1;
  const maxDegree = Math.max(0, ...degree.values());
  const network = nodes.length <= 15 && edges.length <= 24 && density <= 0.15 && labelUniqueness >= 0.5 && maxDegree <= 8;
  return {
    input_node_count: nodes.length,
    input_edge_count: inputEdgeCount,
    display_node_count: nodes.length,
    display_edge_count: edges.length,
    omitted_edge_count: inputEdgeCount - edges.length,
    density,
    label_uniqueness: labelUniqueness,
    max_degree: maxDegree,
    recommended_representation: network ? "network" : "grouped",
  };
}

function normalize(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    typeof item === "bigint" ? Number(item) : item,
  ]));
}
