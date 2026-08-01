export type Relation =
  | "compatible"
  | "complement"
  | "equivalent"
  | "implies"
  | "mutually_exclusive";

export type ExplorerLevel = "component" | "event" | "proposition";

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
  classification_coverage: number;
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

export interface GraphView {
  level: ExplorerLevel;
  nodes: ExplorerNode[];
  edges: ExplorerEdge[];
  truncated_nodes: boolean;
  truncated_edges: boolean;
  coverage: Record<string, unknown>;
}

export type EvidenceTier =
  | "all"
  | "source_contract"
  | "deterministic_rule"
  | "generative_consensus";

export type RecordingEvidenceTier = Exclude<EvidenceTier, "all">;

export interface RecordingScoreBreakdown {
  confidence: number;
  scope: number;
  structural_reach: number;
  evidence_interest: number;
  relation_interest: number;
  confidence_contribution: number;
  scope_contribution: number;
  structural_reach_contribution: number;
  evidence_interest_contribution: number;
  relation_interest_contribution: number;
  base_importance: number;
  same_relation_count: number;
  same_evidence_tier_count: number;
  same_event_pair_count: number;
  shared_endpoint_count: number;
  same_relation_penalty: number;
  same_evidence_tier_penalty: number;
  same_event_pair_penalty: number;
  shared_endpoint_penalty: number;
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
  target_id: string;
  target_label: string;
  target_market_id: string;
  target_event_key: string;
  target_domain: string;
  relation: Relation;
  confidence: number;
  evidence_tier: RecordingEvidenceTier;
  discovery_method: string;
  explanation_excerpt: string;
  importance_score: number;
  score_breakdown: RecordingScoreBreakdown;
}

export interface RecordingPlan {
  schema_version: "oddsfox-recording-plan-v1";
  ranking_version: "balanced-logic-edge-v1";
  graph_fingerprint: string;
  mode: "fast" | "full";
  validation_status: string;
  requested_limit: number;
  min_confidence: number;
  eligible_edge_count: number;
  candidate_pool_size: number;
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
  schema_version: "oddsfox-recording-story-v1";
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
