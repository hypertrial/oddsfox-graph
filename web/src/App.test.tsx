import { describe, expect, it } from "vitest";
import { filterGraphView } from "./api";
import { parseRoute } from "./routes";
import { coverageLabel, marketsByProgressionLevel, marketsForProgressionStage, relationshipSentenceFromParts, safeClaim } from "./human";
import type { GraphView, MarketDetail } from "./types";

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
  coverage: {},
  edge_mode: "all",
  display_stats: null,
};

describe("World Cup outcome explorer", () => {
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
    expect(filterGraphView(fixture, "all", 0.95, false).edges.map((edge) => edge.id)).toEqual([
      "implication",
    ]);
    expect(filterGraphView(fixture, "compatible", 0.98, false).edges.map((edge) => edge.id)).toEqual([
      "compatibility",
    ]);
    expect(filterGraphView(fixture, "implies", 0.98, true).edges).toEqual([]);
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
