import { describe, expect, it, vi } from "vitest";

vi.mock("sigma", () => ({ default: class MockSigma {} }));
vi.mock("@sigma/edge-curve", () => ({
  default: class MockCurveProgram {},
  EdgeCurvedArrowProgram: class MockCurvedArrowProgram {},
  indexParallelEdgesIndex: () => undefined,
}));

import { buildGraph, persistentLabels } from "./GraphCanvas";
import { emptyCoverage } from "./testFixtures";
import type { ExplorerNode, GraphView } from "./types";

function node(
  id: string,
  team: string,
  x: number,
  y: number,
  progressionOutcome: boolean,
): ExplorerNode {
  return {
    id,
    label: progressionOutcome ? `${team} advances` : `${team} does not advance`,
    level: "proposition",
    parent_id: `${team}-event`,
    x,
    y,
    size: 4,
    domain: team,
    component_id: "component",
    market_id: `${id}-market`,
    proposition_count: 1,
    edge_count: 99,
    classification_coverage: null,
    classification_status: "not_applicable",
    progression_outcome: progressionOutcome,
    market_close_epoch: x,
  };
}

const view: GraphView = {
  level: "proposition",
  nodes: [
    node("alpha-early", "Alpha", 10, 0, true),
    node("alpha-late", "Alpha", 40, 0, true),
    node("alpha-no", "Alpha", 50, 4, false),
    node("beta", "Beta", 60, 100, true),
  ],
  edges: [
    {
      id: "path",
      source: "alpha-late",
      target: "alpha-early",
      relation: "implies",
      count: 1,
      confidence: 1,
      discovery_method: "deterministic",
      evidence_tier: "deterministic_rule",
      aggregation_only: false,
    },
    {
      id: "winner-exclusion",
      source: "alpha-late",
      target: "beta",
      relation: "mutually_exclusive",
      count: 1,
      confidence: 1,
      discovery_method: "deterministic",
      evidence_tier: "deterministic_rule",
      aggregation_only: false,
    },
  ],
  layout_mode: "close_time",
  truncated_nodes: false,
  truncated_edges: false,
  coverage: emptyCoverage,
  edge_mode: "essential",
  display_stats: null,
};

describe("graph canvas model", () => {
  it("keeps one short label per team at the latest positive outcome", () => {
    expect([...persistentLabels(view)]).toEqual([
      ["alpha-late", "Alpha"],
      ["beta", "Beta"],
    ]);
  });

  it("sizes from visible links, subdues opposite outcomes, and fans exclusions", () => {
    const graph = buildGraph(view);

    expect(graph.getNodeAttribute("alpha-no", "size")).toBe(3.4);
    expect(graph.getNodeAttribute("alpha-late", "size")).toBeGreaterThan(
      graph.getNodeAttribute("alpha-early", "size"),
    );
    expect(graph.getNodeAttribute("alpha-late", "size")).toBeLessThanOrEqual(8);
    expect(graph.getNodeAttribute("alpha-late", "lightColor")).not.toBe(
      graph.getNodeAttribute("alpha-late", "darkColor"),
    );

    expect(graph.getEdgeAttribute("winner-exclusion", "overviewOpacity")).toBeLessThan(
      graph.getEdgeAttribute("path", "overviewOpacity"),
    );
    expect(Math.abs(graph.getEdgeAttribute("winner-exclusion", "curvature"))).toBeGreaterThan(0.14);
    expect(graph.getEdgeAttribute("winner-exclusion", "curvature")).toBe(
      buildGraph(view).getEdgeAttribute("winner-exclusion", "curvature"),
    );
  });
});
