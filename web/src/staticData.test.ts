import { afterEach, describe, expect, it, vi } from "vitest";

const stats = {
  input_node_count: 0,
  input_edge_count: 0,
  display_node_count: 0,
  display_edge_count: 0,
  omitted_edge_count: 0,
  density: 0,
  label_uniqueness: 1,
  max_degree: 0,
  recommended_representation: "network",
};

const scope = {
  source: "oddsfox-pipeline",
  scope: "wc2026",
  universe: "knockout_progression",
  selection: "all_valid_pipeline_wc2026_markets",
  input_hourly_rows: 0,
  market_count: 0,
  claim_count: 0,
  team_count: 0,
  stage_count: 0,
  first_odds_hour_epoch: null,
  last_odds_hour_epoch: null,
  adapter_version: "polymarket-wc2026-graph-hourly-v1",
  truncated: false,
};

const capabilities = {
  mode: "static",
  hierarchy: true,
  search: true,
  relationship_inspection: true,
  compare: true,
  analyst_graph: true,
  proof: false,
  why_not: false,
  recording: false,
  regeneration: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("static explorer v5 loader", () => {
  it("loads and verifies core JSON before deferring graph JSON", async () => {
    const core = JSON.stringify({
      schema_version: "static-explorer-core-v1",
      scope,
      coverage: {},
      capabilities,
      display_stats: stats,
      stages: [],
      teams: [],
      markets: [],
      claims: [],
      relationships: [],
      essential_relationship_ids: [],
      highlight_relationship_ids: [],
      relationship_groups: [],
    });
    const graph = JSON.stringify({
      schema_version: "static-explorer-graph-v1",
      view: {
        level: "proposition",
        nodes: [],
        edges: [],
        truncated_nodes: false,
        truncated_edges: false,
        coverage: {},
        edge_mode: "all",
        layout_mode: "progression",
        display_stats: stats,
      },
      essential_edge_ids: [],
      layout_version: "visualization-layout-v2",
      coordinate_fingerprint: "fixture",
    });
    const files = {
      "explore_snapshot.json": await fileEntry(core, "static-explorer-core-v1"),
      "graph_snapshot.json": await fileEntry(graph, "static-explorer-graph-v1"),
    };
    const manifest = JSON.stringify({
      schema_version: "static-explorer-v5",
      package_version: "0.13.0",
      source_graph: "source",
      graph_content_fingerprint: "graph",
      build_mode: "fast",
      validation_status: "VALIDATED_FAST",
      coverage: {},
      capabilities,
      tournament_scope: scope,
      display_stats: stats,
      derived_semantics_version: "explorer-derived-semantics-v1",
      essential_projection_version: "essential-projection-v2",
      data_format: "canonical-json-v1",
      files,
    });
    const requested: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const name = String(input).replace(/^\.\//, "");
      requested.push(name);
      const value = name === "static_manifest.json"
        ? manifest
        : name === "explore_snapshot.json"
          ? core
          : name === "graph_snapshot.json"
            ? graph
            : null;
      return value === null
        ? new Response("missing", { status: 404 })
        : new Response(value, { status: 200 });
    }));

    const module = await import("./staticData");
    const loadedCore = await module.loadStaticCoreSnapshot();
    expect("static" in loadedCore.metadata.viewer && loadedCore.metadata.viewer.static).toBe(true);
    expect(requested).toEqual(["static_manifest.json", "explore_snapshot.json"]);

    const loadedGraph = await module.loadStaticSnapshot();
    expect(loadedGraph.view.layout_mode).toBe("progression");
    expect(requested).toEqual([
      "static_manifest.json",
      "explore_snapshot.json",
      "graph_snapshot.json",
    ]);
  });

  it("rejects a core file whose bytes do not match the manifest", async () => {
    const core = JSON.stringify({ schema_version: "static-explorer-core-v1" });
    const graph = JSON.stringify({ schema_version: "static-explorer-graph-v1" });
    const manifest = JSON.stringify({
      schema_version: "static-explorer-v5",
      package_version: "0.13.0",
      source_graph: "source",
      graph_content_fingerprint: "graph",
      build_mode: "fast",
      validation_status: "VALIDATED_FAST",
      coverage: {},
      capabilities,
      tournament_scope: scope,
      display_stats: stats,
      derived_semantics_version: "explorer-derived-semantics-v1",
      essential_projection_version: "essential-projection-v2",
      data_format: "canonical-json-v1",
      files: {
        "explore_snapshot.json": {
          ...(await fileEntry(core, "static-explorer-core-v1")),
          sha256: "0".repeat(64),
        },
        "graph_snapshot.json": await fileEntry(graph, "static-explorer-graph-v1"),
      },
    });
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) =>
      new Response(String(input).includes("static_manifest") ? manifest : core)));

    const module = await import("./staticData");
    await expect(module.loadStaticCoreSnapshot()).rejects.toThrow("integrity validation");
  });
});

async function fileEntry(value: string, schemaVersion: string) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    schema_version: schemaVersion,
    sha256: [...new Uint8Array(digest)]
      .map((item) => item.toString(16).padStart(2, "0"))
      .join(""),
    bytes: bytes.byteLength,
  };
}
