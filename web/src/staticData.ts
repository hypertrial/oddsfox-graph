import * as duckdb from "@duckdb/duckdb-wasm";
import duckdbMvp from "@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url";
import mvpWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url";
import type { ExplorerEdge, ExplorerNode, GraphMetadata, GraphView, SearchNode } from "./types";

interface StaticManifest {
  schema_version: string;
  package_version: string;
  source_graph: string;
  coverage: Record<string, unknown>;
}

interface StaticSnapshot {
  view: GraphView;
  metadata: GraphMetadata;
}

let snapshot: Promise<StaticSnapshot> | null = null;

export function loadStaticSnapshot(): Promise<StaticSnapshot> {
  snapshot ??= load();
  return snapshot;
}

export async function staticSearch(query: string): Promise<SearchNode[]> {
  const loaded = await loadStaticSnapshot();
  const lowered = query.toLocaleLowerCase();
  return loaded.view.nodes
    .filter((node) => node.label.toLocaleLowerCase().includes(lowered) || node.id.toLocaleLowerCase() === lowered)
    .slice(0, 12)
    .map((node) => ({
      node_id: node.id,
      market_id: node.market_id ?? "",
      outcome_label: "",
      event_slug: node.parent_id ?? "",
      canonical_proposition: node.label,
    }));
}

async function load(): Promise<StaticSnapshot> {
  const manifestResponse = await fetch("./static_manifest.json");
  if (!manifestResponse.ok) throw new Error("Static explorer manifest is unavailable");
  const manifest = (await manifestResponse.json()) as StaticManifest;
  if (manifest.schema_version !== "static-explorer-v2") {
    throw new Error(`Unsupported static explorer schema ${manifest.schema_version}`);
  }
  const worker = new Worker(mvpWorker);
  const database = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await database.instantiate(duckdbMvp);
  await database.registerFileURL(
    "snapshot_nodes.parquet",
    new URL("./snapshot_nodes.parquet", window.location.href).href,
    duckdb.DuckDBDataProtocol.HTTP,
    false,
  );
  await database.registerFileURL(
    "snapshot_edges.parquet",
    new URL("./snapshot_edges.parquet", window.location.href).href,
    duckdb.DuckDBDataProtocol.HTTP,
    false,
  );
  const connection = await database.connect();
  try {
    const nodeTable = await connection.query("SELECT * FROM read_parquet('snapshot_nodes.parquet') ORDER BY id");
    const edgeTable = await connection.query("SELECT * FROM read_parquet('snapshot_edges.parquet') ORDER BY id");
    const nodes = nodeTable.toArray().map((row) => normalize(row.toJSON()) as unknown as ExplorerNode);
    const edges = edgeTable.toArray().map((row) => normalize(row.toJSON()) as unknown as ExplorerEdge);
    const level = nodes[0]?.level ?? "proposition";
    const view: GraphView = {
      level,
      nodes,
      edges,
      truncated_nodes: false,
      truncated_edges: false,
      coverage: manifest.coverage,
    };
    return {
      view,
      metadata: {
        package_version: manifest.package_version,
        viewer: { static: true, source_graph: manifest.source_graph },
        coverage: manifest.coverage,
        build: { static: true },
      },
    };
  } finally {
    await connection.close();
    await database.terminate();
  }
}

function normalize(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      typeof item === "bigint" ? Number(item) : item,
    ]),
  );
}
