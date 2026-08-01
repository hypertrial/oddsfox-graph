export type Relation =
  | "compatible"
  | "complement"
  | "equivalent"
  | "implies"
  | "mutually_exclusive";

export type ExplorerLevel = "component" | "event" | "proposition";
export type EdgeMode = "essential" | "all";
export type ClassificationStatus =
  | "not_applicable"
  | "not_started"
  | "partial"
  | "complete";

export interface ExplorerNode {
  id: string;
  label: string;
  level: ExplorerLevel;
  parent_id: string | null;
  x: number;
  y: number;
  size: number;
  domain: string | null;
  component_id: string | null;
  market_id: string | null;
  proposition_count: number;
  edge_count: number;
  classification_coverage: number | null;
  classification_status: ClassificationStatus;
}

export interface ExplorerEdge {
  id: string;
  source: string;
  target: string;
  relation: Relation;
  count: number;
  confidence: number;
  discovery_method: string;
  evidence_tier: string;
  aggregation_only: boolean;
}

export interface GraphDisplayStats {
  input_node_count: number;
  input_edge_count: number;
  display_node_count: number;
  display_edge_count: number;
  omitted_edge_count: number;
  density: number;
  label_uniqueness: number;
  max_degree: number;
  recommended_representation: "network" | "grouped";
}

export interface GraphView {
  level: ExplorerLevel;
  nodes: ExplorerNode[];
  edges: ExplorerEdge[];
  truncated_nodes: boolean;
  truncated_edges: boolean;
  coverage: Record<string, unknown>;
  edge_mode: EdgeMode;
  display_stats: GraphDisplayStats | null;
}

export type EvidenceTier =
  | "all"
  | "source_contract"
  | "deterministic_rule"
  | "generative_consensus";

export type RecordingEvidenceTier = Exclude<EvidenceTier, "all">;

export interface TournamentScope {
  source: "oddsfox-pipeline";
  scope: "wc2026";
  universe: "knockout_progression";
  selection: "all_valid_pipeline_wc2026_markets";
  input_hourly_rows: number;
  market_count: number;
  claim_count: number;
  team_count: number;
  stage_count: number;
  first_odds_hour_epoch: number | null;
  last_odds_hour_epoch: number | null;
  adapter_version: string;
  truncated: false;
}

export interface ExplorerCapabilities {
  mode: "live" | "static";
  hierarchy: boolean;
  search: boolean;
  relationship_inspection: boolean;
  compare: boolean;
  analyst_graph: boolean;
  proof: boolean;
  why_not: boolean;
  recording: boolean;
  regeneration: boolean;
}

export interface ClaimSummary {
  id: string;
  market_id: string;
  canonical_team_name: string;
  stage_key: string;
  stage_rank: number;
  normalized_progression_level: number;
  question: string;
  answer: "Yes" | "No";
  plain_claim: string;
  is_progression_token: boolean;
  market_status: string;
  is_still_alive: boolean | null;
  technical_canonical_label: string;
}

export interface StageSummary {
  stage_key: string;
  label: string;
  stage_rank: number;
  normalized_progression_level: number;
  team_count: number;
  market_count: number;
  claim_count: number;
  active_market_count: number;
  closed_market_count: number;
  classification_eligible_count: number;
  classification_assessed_count: number;
  classification_status: ClassificationStatus;
  classification_coverage: number | null;
}

export interface TeamSummary {
  team_key: string;
  canonical_team_name: string;
  is_still_alive: boolean | null;
  market_status: string | null;
  market_count: number;
  claim_count: number;
  stage_keys: string[];
  min_stage_rank: number;
  max_stage_rank: number;
  classification_eligible_count: number;
  classification_assessed_count: number;
  classification_status: ClassificationStatus;
  classification_coverage: number | null;
}

export interface MarketDetail {
  market_id: string;
  event_slug: string;
  canonical_team_name: string;
  stage_key: string;
  stage_rank: number;
  normalized_progression_level: number;
  question: string;
  market_direction: "winner" | "advance" | "elimination";
  market_status: string;
  is_still_alive: boolean | null;
  claims: ClaimSummary[];
}

export interface RelationshipDetail {
  proposal_id: string;
  source: ClaimSummary;
  target: ClaimSummary;
  relation: Relation;
  basis: string;
  confidence: number;
  evidence_tier: RecordingEvidenceTier;
  discovery_method: string;
  explanation: string;
}

export interface RelationshipGroupSummary {
  id: string;
  title: string;
  description: string;
  relation: Relation;
  member_claim_ids: string[];
  relationship_count: number;
}

export interface HumanHighlight {
  rank: number;
  relationship: RelationshipDetail;
}

export interface ExploreHome {
  scope: TournamentScope;
  stages: StageSummary[];
  teams: TeamSummary[];
  notable_relationships: HumanHighlight[];
  relationship_groups: RelationshipGroupSummary[];
  capabilities: ExplorerCapabilities;
  display_stats: GraphDisplayStats;
  coverage: Record<string, unknown>;
}

export interface StageDetail {
  summary: StageSummary;
  teams: TeamSummary[];
  markets: MarketDetail[];
}

export interface TeamDetail {
  summary: TeamSummary;
  markets: MarketDetail[];
}

export type EntityKind = "team" | "stage" | "market" | "claim";

export interface EntitySearchResult {
  kind: EntityKind;
  id: string;
  label: string;
  description: string;
}

export interface CompareResult {
  status: "same_claim" | "direct" | "path" | "no_proven_relationship";
  source: ClaimSummary;
  target: ClaimSummary;
  direct: RelationshipDetail | null;
  path: RelationshipDetail[];
  explanation: string;
}

export interface Page<T> {
  rows: T[];
  next_cursor: string | null;
  truncated: boolean;
}

export interface RecordingScoreBreakdown {
  confidence: number;
  stage_importance: number;
  structural_reach: number;
  template_novelty: number;
  evidence_interest: number;
  relation_interest: number;
  confidence_contribution: number;
  stage_importance_contribution: number;
  structural_reach_contribution: number;
  template_novelty_contribution: number;
  evidence_interest_contribution: number;
  relation_interest_contribution: number;
  base_importance: number;
  same_relation_count: number;
  same_evidence_tier_count: number;
  same_target_stage_count: number;
  same_component_count: number;
  same_relation_penalty: number;
  same_evidence_tier_penalty: number;
  same_target_stage_penalty: number;
  same_component_penalty: number;
  total_penalty: number;
  selection_score: number;
}

export interface RecordingHighlight {
  rank: number;
  proposal_id: string;
  source_id: string;
  source_label: string;
  source_market_id: string;
  source_event_key: string;
  source_domain: string;
  source_team_name: string;
  source_stage_key: string;
  source_stage_rank: number;
  source_plain_claim: string;
  target_id: string;
  target_label: string;
  target_market_id: string;
  target_event_key: string;
  target_domain: string;
  target_team_name: string;
  target_stage_key: string;
  target_stage_rank: number;
  target_plain_claim: string;
  template_key: string;
  component_id: string;
  relation: Relation;
  confidence: number;
  evidence_tier: RecordingEvidenceTier;
  discovery_method: string;
  explanation_excerpt: string;
  importance_score: number;
  score_breakdown: RecordingScoreBreakdown;
}

export interface RecordingPlan {
  schema_version: "oddsfox-recording-plan-v2";
  ranking_version: "human-wc2026-story-edge-v2";
  graph_fingerprint: string;
  mode: "fast" | "full";
  validation_status: string;
  requested_limit: number;
  min_confidence: number;
  eligible_edge_count: number;
  candidate_pool_size: number;
  excluded_missing_context: number;
  excluded_pathological: number;
  highlights: RecordingHighlight[];
  graph: GraphView;
  context_pruning: {
    incident_edge_cap_per_endpoint: number;
    candidate_nodes: number;
    candidate_edges: number;
    retained_nodes: number;
    retained_edges: number;
    pruned_nodes: number;
    pruned_edges: number;
  };
}

export interface CameraState {
  x: number;
  y: number;
  ratio: number;
  angle: 0;
}

export interface StoryShot {
  kind: "intro" | "highlight" | "outro";
  highlight_index: number | null;
  start_frame: number;
  end_frame: number;
  zoom_end_frame: number;
  reveal_end_frame: number;
  camera_from: CameraState;
  camera_to: CameraState;
}

export interface RecordingStory {
  schema_version: "oddsfox-recording-story-v2";
  graph_fingerprint: string;
  source_fingerprint: string;
  client_version: string;
  client_fingerprint: string;
  layout_version: "hierarchical-fa2-v1";
  layout_fingerprint: string;
  ranking_version: string;
  mode: "fast" | "full";
  validation_status: string;
  graph: GraphView;
  highlights: RecordingHighlight[];
  context_pruning: RecordingPlan["context_pruning"];
  viewport: { width: number; height: number; fps: number };
  timeline: {
    frame_count: number;
    duration_seconds: number;
    intro_seconds: 3;
    highlight_seconds: 7;
    outro_seconds: 3;
    shots: StoryShot[];
  };
  presentation_theme: {
    background: string;
    foreground: string;
    muted: string;
    accent: string;
  };
  layout_metadata: LayoutMetadata;
}

export interface LayoutMetadata {
  version: "hierarchical-fa2-v1";
  iterations: 250;
  quantization_decimals: 4;
  groups: Array<{
    id: string;
    node_count: number;
    barnes_hut: boolean;
    settings: Record<string, number | boolean>;
  }>;
}

export interface StoryFrameState {
  frame: number;
  shot: StoryShot;
  camera: CameraState;
  highlightedEdge: string | null;
  highlightedNodes: ReadonlySet<string>;
  visibleEdges: ReadonlySet<string>;
  visibleNodes: ReadonlySet<string>;
  reveal: number;
  emphasis: number;
  overlay: "intro" | "caption" | "outro";
}

export interface SearchNode {
  node_id: string;
  market_id: string;
  outcome_label: string;
  event_slug: string;
  canonical_proposition: string;
}

export interface GraphMetadata {
  package_version: string;
  viewer: Record<string, unknown>;
  coverage: Record<string, unknown>;
  build: Record<string, unknown>;
}
