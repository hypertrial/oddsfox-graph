import { describe, expect, it } from "vitest";
import { ESSENTIAL_PROJECTION_VERSION, essentialGraphEdges } from "./graphEdges";
import type { ExplorerEdge, Relation } from "./types";

describe("essential projection v2", () => {
  it("removes only equal-or-stronger transitive implications", () => {
    const edges = [
      edge("a-b", "a", "b", 0.95),
      edge("b-c", "b", "c", 0.95),
      edge("a-c", "a", "c", 0.95),
      edge("a-d", "a", "d", 1),
      edge("d-e", "d", "e", 0.9),
      edge("a-e", "a", "e", 0.95),
    ];

    expect(ESSENTIAL_PROJECTION_VERSION).toBe("essential-projection-v2");
    expect(essentialGraphEdges(edges).map((item) => item.id)).toEqual([
      "a-d", "a-b", "a-e", "b-c", "d-e",
    ]);
  });

  it("preserves cycles, symmetric relations, and explicitly selected edges", () => {
    const edges = [
      edge("a-b", "a", "b"),
      edge("b-c", "b", "c"),
      edge("c-a", "c", "a"),
      edge("a-c", "a", "c"),
      edge("same", "a", "b", 1, "equivalent"),
      edge("same-copy", "b", "a", 0.9, "equivalent"),
    ];
    const retained = essentialGraphEdges(edges, new Set(["a-c"]));
    expect(retained.map((item) => item.id)).toEqual([
      "same", "a-b", "a-c", "b-c", "c-a",
    ]);
  });

  it("meets the WC2026 latency and four-times scaling budget", () => {
    const baseline = scaleEdges(1);
    const scaled = scaleEdges(4);
    const baselineMs = medianRuntimeMs(baseline);
    const scaledMs = medianRuntimeMs(scaled);

    expect(baseline).toHaveLength(834);
    expect(baselineMs).toBeLessThanOrEqual(15);
    expect(scaledMs).toBeLessThan(baselineMs * 8);
  });
});

function scaleEdges(copies: number): ExplorerEdge[] {
  const edges: ExplorerEdge[] = [];
  const append = (source: string, target: string, relation: Relation) => {
    edges.push(edge(`scale-${String(edges.length).padStart(5, "0")}`, source, target, 1, relation));
  };
  for (let copy = 0; copy < copies; copy += 1) {
    const prefix = `${copy}:`;
    for (let team = 0; team < 16; team += 1) {
      for (let level = 0; level < 6; level += 1) {
        append(`${prefix}${team}-${level}-yes`, `${prefix}${team}-${level}-no`, "complement");
        for (let lower = 0; lower < level; lower += 1) {
          append(`${prefix}${team}-${level}-yes`, `${prefix}${team}-${lower}-yes`, "implies");
          append(`${prefix}${team}-${lower}-no`, `${prefix}${team}-${level}-no`, "implies");
        }
      }
    }
    for (let left = 0; left < 16; left += 1) {
      for (let right = left + 1; right < 16; right += 1) {
        append(`${prefix}${left}-5-yes`, `${prefix}${right}-5-yes`, "mutually_exclusive");
      }
    }
  }
  while (edges.length < 834 * copies) {
    append(`padding-${edges.length}-a`, `padding-${edges.length}-b`, "compatible");
  }
  return edges;
}

function medianRuntimeMs(edges: ExplorerEdge[]): number {
  for (let warmup = 0; warmup < 3; warmup += 1) essentialGraphEdges(edges);
  const values: number[] = [];
  for (let run = 0; run < 15; run += 1) {
    const started = performance.now();
    essentialGraphEdges(edges);
    values.push(performance.now() - started);
  }
  values.sort((left, right) => left - right);
  return values[Math.floor(values.length / 2)];
}

function edge(
  id: string,
  source: string,
  target: string,
  confidence = 1,
  relation: Relation = "implies",
): ExplorerEdge {
  return {
    id,
    source,
    target,
    relation,
    confidence,
    count: 1,
    discovery_method: "deterministic",
    evidence_tier: "deterministic_rule",
    aggregation_only: false,
  };
}
