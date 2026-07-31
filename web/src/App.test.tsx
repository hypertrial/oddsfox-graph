import { describe, expect, it } from "vitest";
import { filterGraphView } from "./api";
import type { GraphView } from "./types";

const fixture: GraphView = {
  level: "proposition",
  nodes: [],
  edges: [
    {
      id: "implication",
      source: "a",
      target: "b",
      relation: "implies",
      count: 1,
      confidence: 0.97,
      discovery_method: "generative_consensus",
      aggregation_only: false,
    },
    {
      id: "compatibility",
      source: "a",
      target: "c",
      relation: "compatible",
      count: 1,
      confidence: 0.99,
      discovery_method: "deterministic",
      aggregation_only: false,
    },
  ],
  truncated_nodes: false,
  truncated_edges: false,
  coverage: {},
};

describe("logic explorer", () => {
  it("applies relation, confidence, and compatible filters to static exports", () => {
    expect(filterGraphView(fixture, "all", 0.95, false).edges.map((edge) => edge.id)).toEqual([
      "implication",
    ]);
    expect(filterGraphView(fixture, "compatible", 0.98, false).edges.map((edge) => edge.id)).toEqual([
      "compatibility",
    ]);
    expect(filterGraphView(fixture, "implies", 0.98, true).edges).toEqual([]);
  });
});
