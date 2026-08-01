import { describe, expect, it } from "vitest";
import { createLayoutTask } from "./layout";
import { buildStory, captionFor, storyFrame } from "./story";
import type { GraphView, RecordingPlan } from "./types";

const graph: GraphView = {
  level: "proposition",
  nodes: [
    { id: "a", label: "Alpha", level: "proposition", parent_id: "event-a", x: -10, y: 0, size: 4, domain: "sports", component_id: "one", market_id: "m1", proposition_count: 1, edge_count: 1, classification_coverage: 1 },
    { id: "b", label: "Beta", level: "proposition", parent_id: "event-b", x: 10, y: 0, size: 4, domain: "sports", component_id: "one", market_id: "m2", proposition_count: 1, edge_count: 1, classification_coverage: 1 },
  ],
  edges: [{ id: "p1", source: "a", target: "b", relation: "implies", count: 1, confidence: 0.99, discovery_method: "generative_consensus", evidence_tier: "generative_consensus", aggregation_only: false }],
  truncated_nodes: false,
  truncated_edges: false,
  coverage: {},
};

const score = {
  confidence: 0.99, scope: 1, structural_reach: 1, evidence_interest: 1, relation_interest: 1,
  confidence_contribution: 0.297, scope_contribution: 0.25, structural_reach_contribution: 0.2,
  evidence_interest_contribution: 0.15, relation_interest_contribution: 0.1, base_importance: 0.997,
  same_relation_count: 0, same_evidence_tier_count: 0, same_event_pair_count: 0, shared_endpoint_count: 0,
  same_relation_penalty: 0, same_evidence_tier_penalty: 0, same_event_pair_penalty: 0,
  shared_endpoint_penalty: 0, total_penalty: 0, selection_score: 0.997,
};

function plan(highlights = 6): RecordingPlan {
  return {
    schema_version: "oddsfox-recording-plan-v1", ranking_version: "balanced-logic-edge-v1",
    graph_fingerprint: "fixture", mode: "full", validation_status: "VALID", requested_limit: highlights,
    min_confidence: 0.95, eligible_edge_count: highlights, candidate_pool_size: highlights,
    highlights: Array.from({ length: highlights }, (_, index) => ({
      rank: index + 1, proposal_id: "p1", source_id: "a", source_label: "Alpha", source_market_id: "m1",
      source_event_key: "event-a", source_domain: "sports", target_id: "b", target_label: "Beta",
      target_market_id: "m2", target_event_key: "event-b", target_domain: "sports", relation: "implies",
      confidence: 0.99, evidence_tier: "generative_consensus", discovery_method: "generative_consensus",
      explanation_excerpt: "Alpha entails Beta", importance_score: 0.997, score_breakdown: score,
    })),
    graph,
    context_pruning: { incident_edge_cap_per_endpoint: 25, candidate_nodes: 2, candidate_edges: 1, retained_nodes: 2, retained_edges: 1, pruned_nodes: 0, pruned_edges: 0 },
  };
}

describe("deterministic recording story", () => {
  it("uses exactly 1,440 frames for six highlights at 30 fps", () => {
    const story = buildStory(plan(), graph, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    expect(story.timeline.frame_count).toBe(1_440);
    expect(story.timeline.duration_seconds).toBe(48);
    expect(storyFrame(story, 0)).toEqual(storyFrame(story, 0));
    expect(captionFor(story, 90)).toContain("Alpha implies Beta");
  });

  it("keeps zoom, reveal, emphasis, and restoration in distinct frame phases", () => {
    const story = buildStory(plan(1), graph, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    expect(storyFrame(story, 90).highlightedNodes.size).toBe(0);
    expect(storyFrame(story, 90).highlightedEdge).toBeNull();
    expect([...storyFrame(story, 150).highlightedNodes]).toEqual(["a", "b"]);
    expect(storyFrame(story, 150).highlightedEdge).toBeNull();
    expect(storyFrame(story, 179).emphasis).toBe(0);
    expect(storyFrame(story, 180).highlightedEdge).toBe("p1");
    expect(storyFrame(story, 180).emphasis).toBeGreaterThan(0);
    expect(storyFrame(story, story.timeline.frame_count - 1).camera).toEqual({
      x: 0.5,
      y: 0.5,
      ratio: 1,
      angle: 0,
    });
  });

  it("frames only immediate endpoint neighbors rather than cascading through a chain", () => {
    const nodes = [
      { ...graph.nodes[0], id: "a", x: 0 },
      { ...graph.nodes[0], id: "b", x: 10 },
      { ...graph.nodes[0], id: "c", x: 100 },
      { ...graph.nodes[0], id: "d", x: 1000 },
    ];
    const chain: GraphView = {
      ...graph,
      nodes,
      edges: [
        { ...graph.edges[0], id: "p1", source: "a", target: "b" },
        { ...graph.edges[0], id: "p2", source: "b", target: "c" },
        { ...graph.edges[0], id: "p3", source: "c", target: "d" },
      ],
    };
    const chainPlan = { ...plan(1), graph: chain };
    const story = buildStory(chainPlan, chain, "layout", { version: "hierarchical-fa2-v1", iterations: 250, quantization_decimals: 4, groups: [] }, { width: 1920, height: 1080, fps: 30 }, "test", "client");
    expect(story.timeline.shots[1].camera_to.ratio).toBeLessThan(0.5);
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
