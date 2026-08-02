import type { components } from "./generated/api-schema";

type ApiSchemas = components["schemas"];

export type Relation = ApiSchemas["ExplorerEdge"]["relation"];
export type ExplorerLevel = ApiSchemas["ExplorerNode"]["level"];
export type EdgeMode = ApiSchemas["GraphView"]["edge_mode"];
export type ClassificationStatus = ApiSchemas["ExplorerNode"]["classification_status"];
export type ExplorerNode = ApiSchemas["ExplorerNode"];
export type ExplorerEdge = ApiSchemas["ExplorerEdge"];
export type GraphDisplayStats = ApiSchemas["GraphDisplayStats"];
export type CoverageSummary = ApiSchemas["CoverageSummary"];
export type GraphView = ApiSchemas["GraphView"];
export type RecordingEvidenceTier = ApiSchemas["RecordingHighlight"]["evidence_tier"];
export type EvidenceTier = "all" | RecordingEvidenceTier;
export type TournamentScope = ApiSchemas["TournamentScope"];
export type ExplorerCapabilities = ApiSchemas["ExplorerCapabilities"];
export type ClaimSummary = ApiSchemas["ClaimSummary"];
export type StageSummary = ApiSchemas["StageSummary"];
export type TeamSummary = ApiSchemas["TeamSummary"];
export type MarketDetail = ApiSchemas["MarketDetail"];
export type RelationshipDetail = ApiSchemas["RelationshipDetail"];
export type RelationshipGroupSummary = ApiSchemas["RelationshipGroupSummary"];
export type HumanHighlight = ApiSchemas["HumanHighlight"];
export type ExploreHome = ApiSchemas["ExploreHome"];
export type StageDetail = ApiSchemas["StageDetail"];
export type TeamDetail = ApiSchemas["TeamDetail"];
export type EntityKind = ApiSchemas["EntitySearchResult"]["kind"];
export type EntitySearchResult = ApiSchemas["EntitySearchResult"];
export type CompareResult = ApiSchemas["CompareResult"];

export interface Page<T> {
  rows: T[];
  next_cursor: string | null;
  truncated: boolean;
}

export type RecordingScoreBreakdown = ApiSchemas["RecordingScoreBreakdown"];
export type RecordingHighlight = ApiSchemas["RecordingHighlight"];
export type RecordingPlan = ApiSchemas["RecordingPlan"];

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

export type SearchNode = Pick<
  ApiSchemas["Node"],
  | "node_id"
  | "market_id"
  | "outcome_label"
  | "event_slug"
  | "canonical_proposition"
  | "plain_claim"
>;

export type LiveGraphMetadata = ApiSchemas["ExplorerMetadata"];

export interface StaticGraphMetadata {
  package_version: string;
  client_fingerprint?: string | null;
  viewer: {
    static: true;
    source_graph: string;
    graph_content_fingerprint: string;
    build_mode: "fast" | "full";
    validation_status: string;
    client_fingerprint?: string;
    capabilities: ExplorerCapabilities;
    scope: TournamentScope;
  };
  coverage: ApiSchemas["CoverageSummary"];
  build: {
    static: true;
    build_mode: "fast" | "full";
    validation_status: string;
  };
}

export type GraphMetadata = LiveGraphMetadata | StaticGraphMetadata;
