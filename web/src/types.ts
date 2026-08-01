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
  evidence_tier?: string;
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
