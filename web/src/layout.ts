import Graph from "graphology";
import { circlepack } from "graphology-layout";
import type { GraphView, LayoutMetadata } from "./types";

export const LAYOUT_VERSION = "hierarchical-fa2-v1" as const;

interface Position {
  x: number;
  y: number;
}

export interface LayoutResult {
  positions: Record<string, Position>;
  fingerprint: string;
  metadata: LayoutMetadata;
}

export interface LayoutTask {
  key: string;
  seed: LayoutResult;
  result: Promise<LayoutResult>;
  cancel: () => void;
}

interface WorkerResult {
  key: string;
  groups: Array<{
    id: string;
    positions: Record<string, Position>;
    settings: Record<string, number | boolean>;
    nodeCount: number;
  }>;
}

const sessionCache = new Map<string, LayoutResult>();

export function layoutKey(
  view: GraphView,
  graphFingerprint: string,
  activeFilters: string,
): string {
  return stableHash(
    JSON.stringify({
      graphFingerprint,
      level: view.level,
      nodes: view.nodes.map((node) => node.id).sort(),
      edges: view.edges.map((edge) => edge.id).sort(),
      activeFilters,
      version: LAYOUT_VERSION,
    }),
  );
}

export function createLayoutTask(
  view: GraphView,
  graphFingerprint: string,
  activeFilters: string,
  force = false,
): LayoutTask {
  const key = layoutKey(view, graphFingerprint, activeFilters);
  const seedPositions = seedLayout(view, key);
  const seed = makeResult(view, key, seedPositions, []);
  const cached = force ? undefined : sessionCache.get(key);
  if (cached) {
    return { key, seed, result: Promise.resolve(cached), cancel: () => undefined };
  }
  if (view.level === "component" || view.nodes.length < 3) {
    sessionCache.set(key, seed);
    return { key, seed, result: Promise.resolve(seed), cancel: () => undefined };
  }

  const grouped = groupNodes(view);
  const dynamicGroups = [...grouped.entries()].filter(([, nodes]) => nodes.length > 2);
  if (dynamicGroups.length === 0) {
    sessionCache.set(key, seed);
    return { key, seed, result: Promise.resolve(seed), cancel: () => undefined };
  }

  const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), {
    type: "module",
  });
  let cancelled = false;
  const result = new Promise<LayoutResult>((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<WorkerResult>) => {
      if (cancelled || event.data.key !== key) return;
      const local = new Map<string, Record<string, Position>>();
      for (const group of event.data.groups) local.set(group.id, group.positions);
      for (const [groupId, nodes] of grouped) {
        if (nodes.length <= 2) {
          local.set(
            groupId,
            Object.fromEntries(nodes.map((node) => [node.id, seedPositions[node.id]])),
          );
        }
      }
      const positions = packGroups(view, local, key);
      const metadataGroups = [...grouped.entries()].map(([groupId, nodes]) => {
        const workerGroup = event.data.groups.find((item) => item.id === groupId);
        return {
          id: groupId,
          node_count: nodes.length,
          barnes_hut: nodes.length >= 100,
          settings: workerGroup?.settings ?? {},
        };
      });
      const completed = makeResult(view, key, positions, metadataGroups);
      sessionCache.set(key, completed);
      worker.terminate();
      resolve(completed);
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "Graph layout worker failed"));
    };
  });

  worker.postMessage({
    key,
    groups: dynamicGroups.map(([groupId, nodes]) => {
      const nodeIds = new Set(nodes.map((node) => node.id));
      return {
        id: groupId,
        nodes: nodes.map((node) => ({
          id: node.id,
          x: seedPositions[node.id].x,
          y: seedPositions[node.id].y,
          size: Math.max(1, node.size),
        })),
        edges: view.edges
          .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
          .map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            weight: Math.max(1, edge.count),
          })),
      };
    }),
  });

  return {
    key,
    seed,
    result,
    cancel: () => {
      cancelled = true;
      worker.terminate();
    },
  };
}

export async function freezeLayout(
  view: GraphView,
  graphFingerprint: string,
  activeFilters: string,
): Promise<{ view: GraphView; layout: LayoutResult }> {
  const task = createLayoutTask(view, graphFingerprint, activeFilters);
  const layout = await task.result;
  return { view: applyLayout(view, layout), layout };
}

export function applyLayout(view: GraphView, layout: LayoutResult): GraphView {
  return {
    ...view,
    nodes: view.nodes.map((node) => ({
      ...node,
      x: layout.positions[node.id]?.x ?? node.x,
      y: layout.positions[node.id]?.y ?? node.y,
    })),
  };
}

function seedLayout(view: GraphView, key: string): Record<string, Position> {
  if (view.nodes.length === 0) return {};
  if (view.level === "component") {
    const graph = new Graph();
    for (const node of [...view.nodes].sort(byId)) {
      graph.addNode(node.id, { size: Math.max(1, Math.sqrt(node.proposition_count)) });
    }
    return quantizePositions(
      circlepack(graph, { center: 0, scale: 8, rng: seededRandom(key) }),
    );
  }
  const initial: Record<string, Position> = {};
  for (const [groupId, nodes] of groupNodes(view)) {
    const sorted = [...nodes].sort(byId);
    if (sorted.length === 1) {
      initial[sorted[0].id] = { x: 0, y: 0 };
    } else if (sorted.length === 2) {
      const gap = Math.max(8, sorted[0].size + sorted[1].size);
      initial[sorted[0].id] = { x: -gap, y: 0 };
      initial[sorted[1].id] = { x: gap, y: 0 };
    } else {
      const coincident = sorted.every(
        (node) => node.x === sorted[0].x && node.y === sorted[0].y,
      );
      for (const node of sorted) {
        const random = seededRandom(`${key}:${groupId}:${node.id}`);
        initial[node.id] = {
          x: node.x + (coincident ? (random() - 0.5) * 0.01 : 0),
          y: node.y + (coincident ? (random() - 0.5) * 0.01 : 0),
        };
      }
    }
  }
  const locals = new Map<string, Record<string, Position>>();
  for (const [groupId, nodes] of groupNodes(view)) {
    locals.set(
      groupId,
      Object.fromEntries(nodes.map((node) => [node.id, initial[node.id]])),
    );
  }
  return packGroups(view, locals, key);
}

function groupNodes(view: GraphView): Map<string, GraphView["nodes"]> {
  const groups = new Map<string, GraphView["nodes"]>();
  for (const node of [...view.nodes].sort(byId)) {
    const groupId = node.component_id ?? node.parent_id ?? "ungrouped";
    const group = groups.get(groupId) ?? [];
    group.push(node);
    groups.set(groupId, group);
  }
  return groups;
}

function packGroups(
  view: GraphView,
  localPositions: Map<string, Record<string, Position>>,
  key: string,
): Record<string, Position> {
  const groupGraph = new Graph();
  const groups = groupNodes(view);
  for (const [groupId, nodes] of groups) {
    const local = localPositions.get(groupId) ?? {};
    const radius = Math.max(
      1,
      ...nodes.map((node) => {
        const position = local[node.id] ?? { x: 0, y: 0 };
        return Math.hypot(position.x, position.y) + Math.max(1, node.size);
      }),
    );
    groupGraph.addNode(groupId, { size: radius });
  }
  const groupPositions = circlepack(groupGraph, {
    center: 0,
    scale: 1.15,
    rng: seededRandom(`${key}:pack`),
  });
  const result: Record<string, Position> = {};
  for (const [groupId, nodes] of groups) {
    const offset = groupPositions[groupId] ?? { x: 0, y: 0 };
    const local = localPositions.get(groupId) ?? {};
    for (const node of nodes) {
      const position = local[node.id] ?? { x: 0, y: 0 };
      result[node.id] = { x: position.x + offset.x, y: position.y + offset.y };
    }
  }
  return normalizePositions(result);
}

function normalizePositions(positions: Record<string, Position>): Record<string, Position> {
  const sanitized = Object.fromEntries(
    Object.entries(positions).map(([id, position]) => [
      id,
      {
        x: Number.isFinite(position.x) ? position.x : 0,
        y: Number.isFinite(position.y) ? position.y : 0,
      },
    ]),
  );
  const values = Object.values(sanitized);
  if (values.length === 0) return {};
  const minX = Math.min(...values.map((item) => item.x));
  const maxX = Math.max(...values.map((item) => item.x));
  const minY = Math.min(...values.map((item) => item.y));
  const maxY = Math.max(...values.map((item) => item.y));
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const scale = 1_000 / Math.max(1, maxX - minX, maxY - minY);
  return quantizePositions(
    Object.fromEntries(
      Object.entries(sanitized).map(([id, position]) => [
        id,
        { x: (position.x - centerX) * scale, y: (position.y - centerY) * scale },
      ]),
    ),
  );
}

function quantizePositions(positions: Record<string, Position>): Record<string, Position> {
  return Object.fromEntries(
    Object.entries(positions).map(([id, position]) => [
      id,
      { x: quantize(position.x), y: quantize(position.y) },
    ]),
  );
}

function makeResult(
  view: GraphView,
  key: string,
  positions: Record<string, Position>,
  groups: LayoutMetadata["groups"],
): LayoutResult {
  const normalized = normalizePositions(positions);
  return {
    positions: normalized,
    fingerprint: stableHash(
      JSON.stringify(
        Object.entries(normalized).sort(([left], [right]) => left.localeCompare(right)),
      ),
    ),
    metadata: {
      version: LAYOUT_VERSION,
      iterations: 250,
      quantization_decimals: 4,
      groups:
        groups.length > 0
          ? groups
          : [...groupNodes(view).entries()].map(([id, nodes]) => ({
              id,
              node_count: nodes.length,
              barnes_hut: nodes.length >= 100,
              settings: {},
            })),
    },
  };
}

function byId(left: { id: string }, right: { id: string }): number {
  return left.id.localeCompare(right.id);
}

function quantize(value: number): number {
  return Number.isFinite(value) ? Number(value.toFixed(4)) : 0;
}

function stableHash(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `${(first >>> 0).toString(16).padStart(8, "0")}${(second >>> 0).toString(16).padStart(8, "0")}`;
}

function seededRandom(seed: string): () => number {
  let state = Number.parseInt(stableHash(seed).slice(0, 8), 16) || 1;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}
