import { describe, expect, it } from "vitest";
import { filterGraphView } from "./api";
import { parseRoute } from "./routes";
import { coverageLabel, marketsByProgressionLevel, marketsForProgressionStage, relationshipSentenceFromParts, safeClaim } from "./human";
import { closeTimeColumns, layoutGroupId } from "./layout";
import { essentialGraphEdges } from "./graphEdges";
import { emptyCoverage } from "./testFixtures";
import type { GraphView, MarketDetail } from "./types";

const fixture: GraphView = {
  level: "proposition",
  layout_mode: "hierarchical",
  nodes: ["a", "b", "c", "isolated"].map((id) => ({
    id,
    label: id,
    level: "proposition",
    parent_id: null,
    x: 0,
    y: 0,
    size: 1,
    domain: null,
    component_id: null,
    market_id: null,
    proposition_count: 1,
    edge_count: 0,
    classification_coverage: null,
    classification_status: "not_applicable",
  })),
  edges: [
    {
      id: "implication",
      source: "a",
      target: "b",
      relation: "implies",
      count: 1,
      confidence: 0.97,
      discovery_method: "generative_consensus",
      evidence_tier: "generative_consensus",
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
      evidence_tier: "source_contract",
      aggregation_only: false,
    },
  ],
  truncated_nodes: false,
  truncated_edges: false,
  coverage: emptyCoverage,
  edge_mode: "all",
  display_stats: null,
};

describe("World Cup outcome explorer", () => {
  it("assigns market-close columns from earliest to latest", () => {
    const columns = closeTimeColumns([300, 100, 200, 100]);
    expect([...columns.entries()]).toEqual([[100, 0], [200, 1], [300, 2]]);
  });

  it("groups World Cup outcomes by team without changing component identity", () => {
    const brazil = {
      ...fixture.nodes[0],
      domain: "Brazil",
      component_id: "component-tournament",
      progression_outcome: true,
    };
    const argentina = { ...brazil, id: "argentina", domain: "Argentina" };
    expect(layoutGroupId(brazil)).toBe("team:brazil");
    expect(layoutGroupId(argentina)).toBe("team:argentina");
    expect(brazil.component_id).toBe(argentina.component_id);
  });

  it("reduces implications only after the visible edge filter", () => {
    const edges = [
      { ...fixture.edges[0], id: "a-b", source: "a", target: "b", confidence: 1 },
      { ...fixture.edges[0], id: "b-c", source: "b", target: "c", confidence: 1 },
      { ...fixture.edges[0], id: "a-c", source: "a", target: "c", confidence: 1 },
      { ...fixture.edges[0], id: "a-b-equivalent", source: "a", target: "b", relation: "equivalent" as const, confidence: 1 },
    ];
    expect(essentialGraphEdges(edges).map((edge) => edge.id)).not.toContain("a-c");
    expect(essentialGraphEdges(edges.filter((edge) => edge.relation === "implies")))
      .toHaveLength(2);
  });

  it("parses dependency-free hash routes and safely falls back home", () => {
    expect(parseRoute("#/explore/team/brazil")).toEqual({ kind: "team", id: "brazil" });
    expect(parseRoute("#/explore/relationship/proposal%201")).toEqual({ kind: "relationship", id: "proposal 1" });
    expect(parseRoute("#/compare")).toEqual({ kind: "compare" });
    expect(parseRoute("#/analyst")).toEqual({ kind: "analyst" });
    expect(parseRoute("#/unknown")).toEqual({ kind: "home" });
  });

  it("uses human sentences without exposing canonical NOT syntax", () => {
    expect(safeClaim("NOT(Brazil wins the World Cup)")).toBe("It is not true that Brazil wins the World Cup");
    expect(relationshipSentenceFromParts(
      "Brazil wins the World Cup",
      "implies",
      "Brazil reaches the final",
    )).toBe("If Brazil wins the World Cup, then Brazil reaches the final.");
  });

  it("describes partial model review without exposing an exact percentage", () => {
    expect(coverageLabel("partial", 0.437)).toBe("Model review partially complete");
    expect(coverageLabel("partial", 0.437)).not.toContain("43.7");
    expect(coverageLabel("complete", 1)).toBe("Model review complete");
    expect(coverageLabel("not_applicable", null)).toBe("Model review not needed");
  });

  it("applies relation, confidence, and compatible filters to static exports", () => {
    const implications = filterGraphView(fixture, "all", 0.95, false);
    expect(implications.edges.map((edge) => edge.id)).toEqual([
      "implication",
    ]);
    expect(implications.nodes.map((node) => node.id)).toEqual(["a", "b", "c", "isolated"]);
    expect(filterGraphView(fixture, "compatible", 0.98, false).edges.map((edge) => edge.id)).toEqual([
      "compatibility",
    ]);
    expect(filterGraphView(fixture, "compatible", 0.98, false).nodes.map((node) => node.id)).toEqual([
      "a", "c",
    ]);
    expect(filterGraphView(fixture, "implies", 0.98, true).edges).toEqual([]);
    expect(filterGraphView(fixture, "implies", 0.98, true).nodes).toEqual([]);
  });

  it("keeps the default World Cup progression view on positive outcomes", () => {
    const semanticView: GraphView = {
      ...fixture,
      nodes: fixture.nodes.map((node) => ({
        ...node,
        progression_outcome: node.id === "a" || node.id === "b",
      })),
      edges: [
        fixture.edges[0],
        { ...fixture.edges[0], id: "negative", source: "c", target: "isolated" },
      ],
    };
    const filtered = filterGraphView(semanticView, "implies", 0.95, false);
    expect(filtered.edges.map((edge) => edge.id)).toEqual(["implication"]);
    expect(filtered.nodes.map((node) => node.id)).toEqual(["a", "b"]);
    const negative = filterGraphView(semanticView, "implies", 0.95, false, "all", false);
    expect(negative.edges.map((edge) => edge.id)).toEqual(["implication", "negative"]);
    expect(negative.nodes.map((node) => node.id)).toEqual(["a", "b", "c", "isolated"]);
  });

  it("keeps one deterministic representative for every team in filtered views", () => {
    const teamView: GraphView = {
      ...fixture,
      nodes: [
        { ...fixture.nodes[0], id: "brazil-final", domain: "Brazil", progression_outcome: true, progression_level: 4 },
        { ...fixture.nodes[0], id: "brazil-winner", domain: "Brazil", progression_outcome: true, progression_level: 5 },
        { ...fixture.nodes[0], id: "argentina-winner", domain: "Argentina", progression_outcome: true, progression_level: 5 },
        { ...fixture.nodes[0], id: "spain-final", domain: "Spain", progression_outcome: true, progression_level: 4 },
        { ...fixture.nodes[0], id: "spain-winner", domain: "Spain", progression_outcome: true, progression_level: 5 },
      ],
      edges: [{
        ...fixture.edges[0],
        id: "winner-exclusion",
        source: "brazil-winner",
        target: "argentina-winner",
        relation: "mutually_exclusive",
      }],
    };
    const filtered = filterGraphView(teamView, "mutually_exclusive", 0.95, false);
    expect(filtered.nodes.map((node) => node.id)).toEqual([
      "brazil-winner",
      "argentina-winner",
      "spain-winner",
    ]);
  });

  it("keeps every market at its normalized progression level", () => {
    const market = (id: string, stageKey: string): MarketDetail => ({
      market_id: id,
      event_slug: id,
      canonical_team_name: "Brazil",
      stage_key: stageKey,
      stage_rank: 0,
      normalized_progression_level: 1,
      question: `Will Brazil progress in ${id}?`,
      market_direction: "elimination",
      market_status: "active",
      is_still_alive: true,
      market_close_epoch: 1784419200,
      claims: [],
    });
    const grouped = marketsByProgressionLevel([
      market("survive-round-of-32", "round_of_32"),
      market("reach-round-of-16", "round_of_16"),
    ]);
    expect(grouped[0]).toEqual([]);
    expect(grouped[1].map((item) => item.market_id)).toEqual([
      "survive-round-of-32",
      "reach-round-of-16",
    ]);
    expect(marketsForProgressionStage([
      market("survive-round-of-32", "round_of_32"),
      market("reach-round-of-16", "round_of_16"),
    ], 1).map((item) => item.market_id)).toEqual([
      "survive-round-of-32",
      "reach-round-of-16",
    ]);
  });
});
