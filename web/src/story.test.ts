import { describe, expect, it } from "vitest";
import { createLayoutTask } from "./layout";
import { buildStory, captionFor, storyFrame } from "./story";
import type { GraphView, RecordingPlan } from "./types";

const graph: GraphView = {
  level: "proposition",
  nodes: [
    { id: "a", label: "Alpha wins the World Cup", level: "proposition", parent_id: "event-a", x: -10, y: 0, size: 4, domain: "sports", component_id: "one", market_id: "m1", proposition_count: 1, edge_count: 1, classification_coverage: null, classification_status: "not_applicable" },
    { id: "b", label: "Alpha reaches the final", level: "proposition", parent_id: "event-b", x: 10, y: 0, size: 4, domain: "sports", component_id: "one", market_id: "m2", proposition_count: 1, edge_count: 1, classification_coverage: null, classification_status: "not_applicable" },
  ],
  edges: [{ id: "p1", source: "a", target: "b", relation: "implies", count: 1, confidence: 0.99, discovery_method: "deterministic", evidence_tier: "deterministic_rule", aggregation_only: false }],
  truncated_nodes: false,
  truncated_edges: false,
  coverage: {},
  edge_mode: "essential",
  display_stats: null,
};

const score = {
  confidence: 0.99,
  stage_importance: 1,
  structural_reach: 1,
  template_novelty: 1,
  evidence_interest: 0.8,
  relation_interest: 1,
  confidence_contribution: 0.2475,
  stage_importance_contribution: 0.25,
  structural_reach_contribution: 0.2,
  template_novelty_contribution: 0.15,
  evidence_interest_contribution: 0.08,
  relation_interest_contribution: 0.05,
  base_importance: 0.9775,
  same_relation_count: 0,
  same_evidence_tier_count: 0,
  same_target_stage_count: 0,
  same_component_count: 0,
  same_relation_penalty: 0,
  same_evidence_tier_penalty: 0,
  same_target_stage_penalty: 0,
  same_component_penalty: 0,
  total_penalty: 0,
  selection_score: 0.9775,
};

function plan(highlights = 6): RecordingPlan {
  return {
    schema_version: "oddsfox-recording-plan-v2",
    ranking_version: "human-wc2026-story-edge-v2",
    graph_fingerprint: "fixture",
    mode: "fast",
    validation_status: "DETERMINISTIC_VALIDATED",
    requested_limit: highlights,
    min_confidence: 0.95,
    eligible_edge_count: highlights,
    candidate_pool_size: highlights,
    excluded_missing_context: 0,
    excluded_pathological: 0,
    highlights: Array.from({ length: highlights }, (_, index) => ({
      rank: index + 1,
      proposal_id: "p1",
      source_id: "a",
      source_label: "Alpha wins the World Cup",
      source_market_id: "m1",
      source_event_key: "wc2026",
      source_domain: "sports",
      source_team_name: "Alpha",
      source_stage_key: "winner",
      source_stage_rank: 5,
      source_plain_claim: "Alpha wins the World Cup",
      target_id: "b",
      target_label: "Alpha reaches the final",
      target_market_id: "m2",
      target_event_key: "wc2026",
      target_domain: "sports",
      target_team_name: "Alpha",
      target_stage_key: "final",
      target_stage_rank: 4,
      target_plain_claim: "Alpha reaches the final",
      template_key: "winner:yes->final:yes",
      component_id: "one",
      relation: "implies",
      confidence: 0.99,
      evidence_tier: "deterministic_rule",
      discovery_method: "deterministic",
      explanation_excerpt: "Winning requires reaching the final.",
      importance_score: 0.9775,
      score_breakdown: score,
    })),
    graph,
    context_pruning: { incident_edge_cap_per_endpoint: 2, candidate_nodes: 2, candidate_edges: 1, retained_nodes: 2, retained_edges: 1, pruned_nodes: 0, pruned_edges: 0 },
  };
}

describe("deterministic World Cup recording story", () => {
  it("uses exactly 1,440 frames for six highlights at 30 fps", () => {
    const story = buildStory(plan(), graph, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    expect(story.schema_version).toBe("oddsfox-recording-story-v2");
    expect(story.timeline.frame_count).toBe(1_440);
    expect(story.timeline.duration_seconds).toBe(48);
    expect(storyFrame(story, 0)).toEqual(storyFrame(story, 0));
    expect(captionFor(story, 90)).toBe("If Alpha wins the World Cup, then Alpha reaches the final.");
  });

  it("keeps intro, zoom, reveal, emphasis, and outro in distinct frame phases", () => {
    const story = buildStory(plan(1), graph, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    expect(storyFrame(story, 0).overlay).toBe("intro");
    expect(storyFrame(story, 90).highlightedNodes.size).toBe(0);
    expect(storyFrame(story, 90).highlightedEdge).toBeNull();
    expect([...storyFrame(story, 150).highlightedNodes]).toEqual(["a", "b"]);
    expect(storyFrame(story, 150).highlightedEdge).toBeNull();
    expect(storyFrame(story, 179).emphasis).toBe(0);
    expect(storyFrame(story, 180).highlightedEdge).toBe("p1");
    expect(storyFrame(story, 180).emphasis).toBeGreaterThan(0);
    expect(storyFrame(story, story.timeline.frame_count - 1).overlay).toBe("outro");
  });

  it("frames at most four context neighbors rather than a dense component", () => {
    const nodes = Array.from({ length: 9 }, (_, index) => ({
      ...graph.nodes[0],
      id: String.fromCharCode(97 + index),
      x: index === 8 ? 10_000 : index * 10,
    }));
    const dense: GraphView = {
      ...graph,
      nodes,
      edges: nodes.slice(1).map((node, index) => ({ ...graph.edges[0], id: `p${index + 1}`, source: "a", target: node.id, confidence: 1 - index / 100 })),
    };
    const story = buildStory({ ...plan(1), graph: dense }, dense, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    const firstShot = storyFrame(story, 90);
    expect(firstShot.visibleNodes.size).toBe(6);
    expect(firstShot.visibleEdges.size).toBe(5);
    expect(firstShot.visibleEdges.has("p1")).toBe(true);
    expect(firstShot.visibleNodes.has("i")).toBe(false);
    expect(story.timeline.shots[1].camera_to.ratio).toBeLessThan(0.1);
  });

  it("packs component overviews identically for identical inputs", async () => {
    const componentView: GraphView = {
      ...graph,
      level: "component",
      nodes: graph.nodes.map((node) => ({ ...node, level: "component", parent_id: null })),
    };
    const first = await createLayoutTask(componentView, "fixture", "all").result;
    const second = await createLayoutTask(componentView, "fixture", "all").result;
    expect(first.positions).toEqual(second.positions);
    expect(first.fingerprint).toBe(second.fingerprint);
  });
});
