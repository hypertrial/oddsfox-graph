import { marketsForProgressionStage, relationshipSentence } from "./human";
import type {
  ClaimSummary,
  CompareResult,
  CoverageSummary,
  EntitySearchResult,
  ExplorerCapabilities,
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

const STATIC_SCHEMA = "static-explorer-v5";
const CORE_SCHEMA = "static-explorer-core-v1";
const GRAPH_SCHEMA = "static-explorer-graph-v1";
const DERIVED_SEMANTICS_VERSION = "explorer-derived-semantics-v1";
const ESSENTIAL_PROJECTION_VERSION = "essential-projection-v2";

interface StaticFile {
  schema_version: string;
  sha256: string;
  bytes: number;
}

interface StaticManifest {
  schema_version: string;
  package_version: string;
  source_graph: string;
  graph_content_fingerprint: string;
  client_fingerprint: string;
  build_mode: "fast" | "full";
  validation_status: string;
  coverage: CoverageSummary;
  capabilities: ExplorerCapabilities;
  tournament_scope: TournamentScope;
  display_stats: GraphDisplayStats;
  derived_semantics_version: string;
  essential_projection_version: string;
  data_format: string;
  files: Record<string, StaticFile>;
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

interface StaticCorePayload {
  schema_version: string;
  scope: TournamentScope;
  coverage: CoverageSummary;
  capabilities: ExplorerCapabilities;
  display_stats: GraphDisplayStats;
  stages: StageSummary[];
  teams: TeamSummary[];
  markets: MarketRow[];
  claims: ClaimSummary[];
  relationships: RelationshipRow[];
  essential_relationship_ids: string[];
  highlight_relationship_ids: string[];
  relationship_groups: RelationshipGroupSummary[];
}

interface StaticGraphPayload {
  schema_version: string;
  view: GraphView;
  essential_edge_ids: string[];
  layout_version: string;
  coordinate_fingerprint: string;
}

export interface StaticCoreSnapshot {
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
  highlights: HumanHighlight[];
  groups: RelationshipGroupSummary[];
}

export interface StaticSnapshot extends StaticCoreSnapshot {
  view: GraphView;
  essentialEdgeIds: ReadonlySet<string>;
}

let manifestPromise: Promise<StaticManifest> | null = null;
let corePromise: Promise<StaticCoreSnapshot> | null = null;
let graphPromise: Promise<StaticSnapshot> | null = null;

export function loadStaticCoreSnapshot(): Promise<StaticCoreSnapshot> {
  corePromise ??= loadCore();
  return corePromise;
}

export function loadStaticSnapshot(): Promise<StaticSnapshot> {
  graphPromise ??= loadGraph();
  return graphPromise;
}

export async function staticExploreHome(
  teamLimit = 24,
  highlightLimit = 6,
): Promise<ExploreHome> {
  const loaded = await loadStaticCoreSnapshot();
  return {
    scope: loaded.scope,
    stages: loaded.stages,
    teams: loaded.teams.slice(0, teamLimit),
    notable_relationships: loaded.highlights.slice(0, highlightLimit),
    relationship_groups: loaded.groups,
    capabilities: loaded.capabilities,
    display_stats: loaded.displayStats,
    coverage: loaded.metadata.coverage,
  };
}

export async function staticStageDetail(stageKey: string): Promise<StageDetail> {
  const loaded = await loadStaticCoreSnapshot();
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
  const loaded = await loadStaticCoreSnapshot();
  const summary = loaded.teams.find((team) => team.team_key === teamKey);
  if (!summary) throw new Error(`Team ${teamKey} is not in this snapshot`);
  return {
    summary,
    markets: loaded.markets.filter(
      (market) => market.canonical_team_name === summary.canonical_team_name,
    ),
  };
}

export async function staticMarketDetail(marketId: string): Promise<MarketDetail> {
  const market = (await loadStaticCoreSnapshot()).markets.find(
    (item) => item.market_id === marketId,
  );
  if (!market) throw new Error(`Market ${marketId} is not in this snapshot`);
  return market;
}

export async function staticRelationshipDetail(
  proposalId: string,
): Promise<RelationshipDetail> {
  const relationship = (await loadStaticCoreSnapshot()).relationships.find(
    (item) => item.proposal_id === proposalId,
  );
  if (!relationship) {
    throw new Error(`Relationship ${proposalId} is not in this snapshot`);
  }
  return relationship;
}

export async function staticEntitySearch(
  query: string,
  limit = 12,
): Promise<EntitySearchResult[]> {
  const loaded = await loadStaticCoreSnapshot();
  const lowered = query.trim().toLocaleLowerCase();
  if (!lowered) return [];
  const candidates: Array<{ position: number; result: EntitySearchResult }> = [];
  const add = (
    kind: EntitySearchResult["kind"],
    id: string,
    label: string,
    description: string,
  ) => {
    const position = `${label} ${description}`.toLocaleLowerCase().indexOf(lowered);
    if (position >= 0) candidates.push({ position, result: { kind, id, label, description } });
  };
  for (const team of loaded.teams) {
    add("team", team.team_key, team.canonical_team_name, `${team.market_count} progression markets`);
  }
  for (const stage of loaded.stages) {
    add("stage", stage.stage_key, stage.label, `${stage.team_count} teams`);
  }
  for (const market of loaded.markets) {
    add("market", market.market_id, market.question, `${market.canonical_team_name} · ${market.stage_key.replaceAll("_", " ")}`);
  }
  for (const claim of loaded.claims) {
    add("claim", claim.id, claim.plain_claim, `${claim.answer} outcome`);
  }
  return candidates
    .sort((left, right) => left.position - right.position
      || left.result.label.localeCompare(right.result.label)
      || left.result.id.localeCompare(right.result.id))
    .slice(0, limit)
    .map(({ result }) => result);
}

export async function staticCompare(
  sourceId: string,
  targetId: string,
  maxHops = 4,
): Promise<CompareResult> {
  const loaded = await loadStaticCoreSnapshot();
  const source = loaded.claims.find((claim) => claim.id === sourceId);
  const target = loaded.claims.find((claim) => claim.id === targetId);
  if (!source || !target) {
    throw new Error("Both comparison outcomes must exist in this snapshot");
  }
  if (sourceId === targetId) {
    return { status: "same_claim", source, target, direct: null, path: [], explanation: "You selected the same outcome twice." };
  }
  const direct = directRelationship(loaded.relationships, sourceId, targetId);
  if (direct) {
    return { status: "direct", source, target, direct, path: [], explanation: relationshipSentence(direct) };
  }
  const path = shortestPath(loaded.essentialRelationships, sourceId, targetId, maxHops);
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
  const loaded = await loadStaticCoreSnapshot();
  const lowered = query.toLocaleLowerCase();
  const marketById = new Map(loaded.markets.map((market) => [market.market_id, market]));
  const seenClaims = new Set<string>();
  return loaded.claims
    .filter((claim) => `${claim.plain_claim} ${claim.question} ${claim.technical_canonical_label}`.toLocaleLowerCase().includes(lowered)
      || claim.id.toLocaleLowerCase() === lowered)
    .sort((left, right) => Number(right.is_progression_token) - Number(left.is_progression_token)
      || right.normalized_progression_level - left.normalized_progression_level
      || left.plain_claim.localeCompare(right.plain_claim)
      || left.id.localeCompare(right.id))
    .filter((claim) => {
      if (seenClaims.has(claim.plain_claim)) return false;
      seenClaims.add(claim.plain_claim);
      return true;
    })
    .slice(0, 12)
    .map((claim) => ({
      node_id: claim.id,
      market_id: claim.market_id,
      outcome_label: claim.answer,
      event_slug: marketById.get(claim.market_id)?.event_slug ?? "",
      canonical_proposition: claim.plain_claim,
      plain_claim: claim.plain_claim,
    }));
}

async function loadManifest(): Promise<StaticManifest> {
  manifestPromise ??= (async () => {
    const response = await fetch("./static_manifest.json");
    if (!response.ok) throw new Error("Static explorer manifest is unavailable");
    const value = await response.json() as StaticManifest;
    if (value.schema_version !== STATIC_SCHEMA) {
      throw new Error(`Unsupported static explorer schema ${value.schema_version}. Regenerate this snapshot with the current oddsfox-graph release.`);
    }
    if (value.data_format !== "canonical-json-v1") {
      throw new Error(`Unsupported static explorer data format ${value.data_format}`);
    }
    if (value.derived_semantics_version !== DERIVED_SEMANTICS_VERSION
      || value.essential_projection_version !== ESSENTIAL_PROJECTION_VERSION) {
      throw new Error("Static explorer derived-semantics versions do not match this client");
    }
    for (const file of ["explore_snapshot.json", "graph_snapshot.json"]) {
      const entry = value.files?.[file];
      if (!entry?.sha256 || !Number.isInteger(entry.bytes) || entry.bytes < 1) {
        throw new Error(`Static explorer manifest is missing ${file}`);
      }
    }
    return value;
  })();
  return manifestPromise;
}

async function loadCore(): Promise<StaticCoreSnapshot> {
  const manifest = await loadManifest();
  const payload = await verifiedJson<StaticCorePayload>(
    "explore_snapshot.json",
    manifest.files["explore_snapshot.json"],
  );
  if (payload.schema_version !== CORE_SCHEMA) {
    throw new Error(`Unsupported static core schema ${payload.schema_version}`);
  }
  for (const field of [payload.stages, payload.teams, payload.markets, payload.claims, payload.relationships]) {
    if (!Array.isArray(field)) throw new Error("Static core payload is malformed");
  }
  const claimById = uniqueMap(payload.claims, (claim) => claim.id, "claim");
  const markets = payload.markets.map((market) => ({
    ...market,
    claims: payload.claims
      .filter((claim) => claim.market_id === market.market_id)
      .sort((left, right) => Number(right.is_progression_token) - Number(left.is_progression_token)
        || left.id.localeCompare(right.id)),
  }));
  const relationships = payload.relationships.map((row) => {
    const source = claimById.get(row.source_id);
    const target = claimById.get(row.target_id);
    if (!source || !target) {
      throw new Error(`Static relationship ${row.proposal_id} references a missing claim`);
    }
    return { ...row, source, target } satisfies RelationshipDetail;
  });
  const relationshipById = uniqueMap(
    relationships,
    (relationship) => relationship.proposal_id,
    "relationship",
  );
  const essentialRelationships = resolveRelationships(
    payload.essential_relationship_ids,
    relationshipById,
    "essential relationship",
  );
  const highlights = resolveRelationships(
    payload.highlight_relationship_ids,
    relationshipById,
    "highlight relationship",
  ).map((relationship, index) => ({ rank: index + 1, relationship }));
  return {
    scope: payload.scope,
    capabilities: payload.capabilities,
    displayStats: payload.display_stats,
    stages: payload.stages,
    teams: payload.teams,
    markets,
    claims: payload.claims,
    relationships,
    essentialRelationships,
    highlights,
    groups: payload.relationship_groups,
    metadata: {
      package_version: manifest.package_version,
      client_fingerprint: manifest.client_fingerprint,
      viewer: {
        static: true,
        source_graph: manifest.source_graph,
        graph_content_fingerprint: manifest.graph_content_fingerprint,
        build_mode: manifest.build_mode,
        validation_status: manifest.validation_status,
        client_fingerprint: manifest.client_fingerprint,
        capabilities: payload.capabilities,
        scope: payload.scope,
      },
      coverage: payload.coverage,
      build: {
        static: true,
        build_mode: manifest.build_mode,
        validation_status: manifest.validation_status,
      },
    },
  };
}

async function loadGraph(): Promise<StaticSnapshot> {
  const [manifest, core] = await Promise.all([loadManifest(), loadStaticCoreSnapshot()]);
  const payload = await verifiedJson<StaticGraphPayload>(
    "graph_snapshot.json",
    manifest.files["graph_snapshot.json"],
  );
  if (payload.schema_version !== GRAPH_SCHEMA) {
    throw new Error(`Unsupported static graph schema ${payload.schema_version}`);
  }
  if (!Array.isArray(payload.view?.nodes) || !Array.isArray(payload.view?.edges)) {
    throw new Error("Static graph payload is malformed");
  }
  const nodeIds = new Set(payload.view.nodes.map((node) => node.id));
  const edgeIds = new Set<string>();
  for (const edge of payload.view.edges) {
    if (edgeIds.has(edge.id)) throw new Error(`Static graph contains duplicate edge ${edge.id}`);
    edgeIds.add(edge.id);
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      throw new Error(`Static graph edge ${edge.id} references a missing node`);
    }
  }
  for (const edgeId of payload.essential_edge_ids) {
    if (!edgeIds.has(edgeId)) throw new Error(`Static essential edge ${edgeId} is missing`);
  }
  return {
    ...core,
    view: payload.view,
    essentialEdgeIds: new Set(payload.essential_edge_ids),
  };
}

async function verifiedJson<T>(name: string, expected: StaticFile): Promise<T> {
  const response = await fetch(`./${name}`);
  if (!response.ok) throw new Error(`Static explorer file ${name} is unavailable`);
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== expected.bytes) {
    throw new Error(`Static explorer file ${name} has the wrong size`);
  }
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const actual = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== expected.sha256) {
    throw new Error(`Static explorer file ${name} failed integrity validation`);
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as T;
  } catch {
    throw new Error(`Static explorer file ${name} is not valid JSON`);
  }
}

function uniqueMap<T>(
  values: T[],
  key: (value: T) => string,
  label: string,
): Map<string, T> {
  const result = new Map<string, T>();
  for (const value of values) {
    const id = key(value);
    if (result.has(id)) throw new Error(`Static snapshot contains duplicate ${label} ${id}`);
    result.set(id, value);
  }
  return result;
}

function resolveRelationships(
  ids: string[],
  byId: Map<string, RelationshipDetail>,
  label: string,
): RelationshipDetail[] {
  return ids.map((id) => {
    const relationship = byId.get(id);
    if (!relationship) throw new Error(`Static ${label} ${id} is missing`);
    return relationship;
  });
}

function directRelationship(
  relationships: RelationshipDetail[],
  sourceId: string,
  targetId: string,
): RelationshipDetail | null {
  return relationships
    .filter((item) => (item.source.id === sourceId && item.target.id === targetId)
      || (item.relation !== "implies" && item.source.id === targetId && item.target.id === sourceId))
    .sort((left, right) => right.confidence - left.confidence
      || left.proposal_id.localeCompare(right.proposal_id))[0] ?? null;
}

function shortestPath(
  relationships: RelationshipDetail[],
  sourceId: string,
  targetId: string,
  maxHops: number,
): RelationshipDetail[] {
  const adjacency = new Map<string, Array<{ target: string; relationship: RelationshipDetail }>>();
  for (const relationship of relationships) {
    if (relationship.relation !== "implies" && relationship.relation !== "equivalent") continue;
    adjacency.set(relationship.source.id, [
      ...(adjacency.get(relationship.source.id) ?? []),
      { target: relationship.target.id, relationship },
    ]);
    if (relationship.relation === "equivalent") {
      adjacency.set(relationship.target.id, [
        ...(adjacency.get(relationship.target.id) ?? []),
        { target: relationship.source.id, relationship },
      ]);
    }
  }
  for (const values of adjacency.values()) {
    values.sort((left, right) => right.relationship.confidence - left.relationship.confidence
      || left.relationship.proposal_id.localeCompare(right.relationship.proposal_id));
  }
  const queue: Array<{ id: string; path: RelationshipDetail[] }> = [{ id: sourceId, path: [] }];
  const bestHops = new Map([[sourceId, 0]]);
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current.path.length >= maxHops) continue;
    for (const next of adjacency.get(current.id) ?? []) {
      const path = [...current.path, next.relationship];
      if (next.target === targetId) return path;
      if ((bestHops.get(next.target) ?? maxHops + 1) <= path.length) continue;
      bestHops.set(next.target, path.length);
      queue.push({ id: next.target, path });
    }
  }
  return [];
}
