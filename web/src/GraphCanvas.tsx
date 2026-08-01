import { useEffect, useLayoutEffect, useRef } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import EdgeCurveProgram, {
  EdgeCurvedArrowProgram,
  indexParallelEdgesIndex,
} from "@sigma/edge-curve";
import { applyLayout, createLayoutTask } from "./layout";
import { storyFrame } from "./story";
import type { GraphView, RecordingStory, StoryFrameState } from "./types";

const relationColors: Record<string, string> = {
  implies: "#5b91f5",
  equivalent: "#39b9aa",
  complement: "#e27a72",
  mutually_exclusive: "#b889e8",
  compatible: "#8793a5",
};

const domainColors = [
  "#6ea8fe",
  "#57c4ad",
  "#f2a65a",
  "#d98bd8",
  "#f27688",
  "#8ea6df",
  "#9bc66d",
  "#d6a66d",
];

interface RenderState {
  selectedId: string | null;
  hoveredId: string | null;
  story: StoryFrameState | null;
  highestDegree: ReadonlySet<string>;
}

interface Props {
  view: GraphView | null;
  selectedId: string | null;
  graphFingerprint: string;
  filterKey: string;
  layoutNonce: number;
  story: RecordingStory | null;
  frame: number;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onOpenNode?: (id: string) => void;
  onReady?: () => void;
}

export function GraphCanvas({
  view,
  selectedId,
  graphFingerprint,
  filterKey,
  layoutNonce,
  story,
  frame,
  onSelectNode,
  onSelectEdge,
  onOpenNode,
  onReady,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const rendererRef = useRef<Sigma | null>(null);
  const callbacks = useRef({ onSelectNode, onSelectEdge, onOpenNode, onReady });
  const renderState = useRef<RenderState>({
    selectedId: null,
    hoveredId: null,
    story: null,
    highestDegree: new Set(),
  });
  const activeView = useRef(0);
  const appliedLayoutNonce = useRef(layoutNonce);
  const hiddenForStory = story ? storyFrame(story, frame).overlay !== "caption" : false;

  useLayoutEffect(() => {
    callbacks.current = { onSelectNode, onSelectEdge, onOpenNode, onReady };
  }, [onSelectNode, onSelectEdge, onOpenNode, onReady]);

  useEffect(() => {
    if (!container.current) return undefined;
    const graph = new Graph({ multi: true, type: "directed" });
    graphRef.current = graph;
    const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
    const renderer = new Sigma(graph, container.current, {
      allowInvalidContainer: false,
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 7,
      labelDensity: 0.7,
      labelColor: { color: colorScheme.matches ? "#dce5f2" : "#243044" },
      defaultEdgeType: "curved",
      enableEdgeEvents: true,
      edgeProgramClasses: {
        curved: EdgeCurveProgram,
        curvedArrow: EdgeCurvedArrowProgram,
      },
      nodeReducer: (node, data) =>
        reduceNode(graphRef.current ?? graph, renderState.current, node, data),
      edgeReducer: (edge, data) =>
        reduceEdge(graphRef.current ?? graph, renderState.current, edge, data),
    });
    rendererRef.current = renderer;
    renderer.on("clickNode", ({ node }) => callbacks.current.onSelectNode(node));
    renderer.on("clickEdge", ({ edge }) => callbacks.current.onSelectEdge(edge));
    renderer.on("doubleClickNode", ({ node }) => callbacks.current.onOpenNode?.(node));
    renderer.on("enterNode", ({ node }) => {
      renderState.current.hoveredId = node;
      renderer.scheduleRefresh();
    });
    renderer.on("leaveNode", () => {
      renderState.current.hoveredId = null;
      renderer.scheduleRefresh();
    });
    const updateLabelColor = () => {
      renderer.setSetting("labelColor", {
        color: colorScheme.matches ? "#dce5f2" : "#243044",
      });
      renderer.scheduleRefresh();
    };
    colorScheme.addEventListener("change", updateLabelColor);
    return () => {
      activeView.current += 1;
      colorScheme.removeEventListener("change", updateLabelColor);
      renderer.kill();
      rendererRef.current = null;
      graphRef.current = null;
    };
  }, []);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer || !view) return undefined;
    const viewGeneration = activeView.current + 1;
    activeView.current = viewGeneration;
    const forceLayout = layoutNonce !== appliedLayoutNonce.current;
    appliedLayoutNonce.current = layoutNonce;
    let cancelTween: () => void = () => undefined;
    const task = story
      ? null
      : createLayoutTask(view, graphFingerprint, filterKey, forceLayout);
    const initialView = story ? view : applyLayout(view, task!.seed);
    const graph = buildGraph(initialView);
    graphRef.current = graph;
    renderState.current.highestDegree = new Set(
      [...view.nodes]
        .sort((left, right) => right.edge_count - left.edge_count || left.id.localeCompare(right.id))
        .slice(0, 12)
        .map((node) => node.id),
    );
    renderer.setGraph(graph);
    renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 });
    renderer.refresh();
    if (story) {
      callbacks.current.onReady?.();
      return undefined;
    }
    void task!.result
      .then((layout) => {
        if (activeView.current !== viewGeneration) return;
        const next = applyLayout(view, layout);
        const positions = Object.fromEntries(next.nodes.map((node) => [node.id, node]));
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduceMotion) {
          applyPositions(graph, positions);
          renderer.refresh();
          callbacks.current.onReady?.();
          return;
        }
        cancelTween = tweenPositions(
          graph,
          renderer,
          positions,
          600,
          () => callbacks.current.onReady?.(),
        );
      })
      .catch((reason: unknown) => {
        if (activeView.current === viewGeneration) {
          console.error("Graph layout failed", reason);
          callbacks.current.onReady?.();
        }
      });
    return () => {
      task?.cancel();
      cancelTween();
    };
  }, [view, graphFingerprint, filterKey, layoutNonce, story]);

  useLayoutEffect(() => {
    renderState.current.selectedId = selectedId;
    const renderer = rendererRef.current;
    if (renderer) renderer.scheduleRefresh();
  }, [selectedId]);

  useLayoutEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    const state = story ? storyFrame(story, frame) : null;
    renderState.current.story = state;
    if (state) renderer.getCamera().setState(state.camera);
    renderer.refresh();
  }, [story, frame]);

  return (
    <div
      className="graph-canvas"
      ref={container}
      role="img"
      aria-hidden={hiddenForStory || undefined}
      aria-label={`Interactive logic graph with ${view?.nodes.length ?? 0} nodes and ${view?.edges.length ?? 0} relations. Double-click a component or event to zoom in.`}
    />
  );
}

function buildGraph(view: GraphView): Graph {
  const graph = new Graph({ multi: true, type: "directed" });
  for (const node of view.nodes) {
    const degreeValue = view.level === "component" ? node.proposition_count : node.edge_count;
    const bounds = view.level === "proposition" ? [4, 15] : view.level === "event" ? [7, 22] : [10, 30];
    graph.addNode(node.id, {
      label: node.label,
      x: node.x,
      y: node.y,
      size: clamp(Math.sqrt(Math.max(1, degreeValue)) * 3, bounds[0], bounds[1]),
      color: domainColor(node.domain ?? node.component_id ?? node.id),
      degree: node.edge_count,
    });
  }
  for (const edge of view.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    const color = evidenceColor(relationColors[edge.relation], edge.evidence_tier);
    graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
      color,
      baseColor: color,
      size: clamp(Math.log2(edge.count + 1), 0.7, 5),
      baseSize: clamp(Math.log2(edge.count + 1), 0.7, 5),
      type: edge.relation === "implies" ? "curvedArrow" : "curved",
      label: `${edge.relation.replaceAll("_", " ")} · ${edge.count}`,
      confidence: edge.confidence,
      relation: edge.relation,
    });
  }
  indexParallelEdgesIndex(graph);
  graph.forEachEdge((edge, attributes) => {
    const parallel = typeof attributes.parallelIndex === "number" ? attributes.parallelIndex : 0;
    graph.setEdgeAttribute(edge, "curvature", parallel === 0 ? 0.08 : parallel * 0.16);
  });
  return graph;
}

function reduceNode(graph: Graph, state: RenderState, node: string, data: Record<string, unknown>) {
  if (state.story && !state.story.visibleNodes.has(node)) return { ...data, hidden: true };
  const focus = focusedNodes(graph, state);
  const relevant = focus.size === 0 || focus.has(node) || [...focus].some((id) => graph.hasNode(id) && graph.areNeighbors(node, id));
  const forced = focus.has(node) || state.highestDegree.has(node);
  const storyEndpoint = Boolean(state.story?.highlightedNodes.has(node));
  return {
    ...data,
    label: forced ? String(data.label ?? node) : null,
    forceLabel: forced,
    highlighted: focus.has(node) && !state.story,
    size: storyEndpoint
      ? Number(data.size ?? 1) * (1 + 0.35 * (state.story?.reveal ?? 0))
      : Number(data.size ?? 1),
    color: relevant ? String(data.color) : withAlpha(String(data.color), 0.14),
    zIndex: focus.has(node) ? 3 : relevant ? 1 : 0,
  };
}

function reduceEdge(graph: Graph, state: RenderState, edge: string, data: Record<string, unknown>) {
  if (state.story && !state.story.visibleEdges.has(edge)) return { ...data, hidden: true };
  const [source, target] = graph.extremities(edge);
  const storyEdge = state.story?.highlightedEdge;
  const focus = focusedNodes(graph, state);
  const relevant = storyEdge
    ? edge === storyEdge || state.story!.highlightedNodes.has(source) || state.story!.highlightedNodes.has(target)
    : focus.size === 0 || edge === state.selectedId || focus.has(source) || focus.has(target);
  const emphasized = storyEdge === edge;
  const confidence = Number(data.confidence ?? 1);
  const baseSize = Number(data.baseSize ?? 1);
  return {
    ...data,
    color: withAlpha(String(data.baseColor), relevant ? clamp(confidence, 0.28, 1) : 0.07),
    size: emphasized ? baseSize * (1 + 2.2 * (state.story?.emphasis ?? 0)) : baseSize,
    zIndex: emphasized ? 4 : relevant ? 1 : 0,
  };
}

function focusedNodes(graph: Graph, state: RenderState): ReadonlySet<string> {
  if (state.story?.highlightedNodes.size) return state.story.highlightedNodes;
  const nodes = new Set<string>();
  for (const id of [state.hoveredId, state.selectedId]) {
    if (!id) continue;
    if (graph.hasNode(id)) nodes.add(id);
    else if (graph.hasEdge(id)) {
      const [source, target] = graph.extremities(id);
      nodes.add(source);
      nodes.add(target);
    }
  }
  return nodes;
}

function applyPositions(graph: Graph, positions: Record<string, { x: number; y: number }>) {
  graph.updateEachNodeAttributes((node, attributes) => ({
    ...attributes,
    x: positions[node]?.x ?? attributes.x,
    y: positions[node]?.y ?? attributes.y,
  }));
}

function tweenPositions(
  graph: Graph,
  renderer: Sigma,
  target: Record<string, { x: number; y: number }>,
  duration: number,
  complete: () => void,
): () => void {
  const source = Object.fromEntries(
    graph.mapNodes((node, attributes) => [node, { x: Number(attributes.x), y: Number(attributes.y) }]),
  );
  const started = performance.now();
  let animation = 0;
  let cancelled = false;
  const tick = (now: number) => {
    if (cancelled) return;
    const container = renderer.getContainer();
    if (!container.isConnected || container.clientWidth === 0 || container.clientHeight === 0) {
      cancelled = true;
      return;
    }
    const progress = clamp((now - started) / duration, 0, 1);
    const eased = 1 - (1 - progress) ** 3;
    graph.updateEachNodeAttributes((node, attributes) => ({
      ...attributes,
      x: lerp(source[node].x, target[node]?.x ?? source[node].x, eased),
      y: lerp(source[node].y, target[node]?.y ?? source[node].y, eased),
    }));
    renderer.refresh();
    if (progress < 1) animation = requestAnimationFrame(tick);
    else complete();
  };
  animation = requestAnimationFrame(tick);
  return () => {
    cancelled = true;
    cancelAnimationFrame(animation);
  };
}

function domainColor(domain: string): string {
  let hash = 0;
  for (const character of domain) hash = Math.imul(hash ^ character.charCodeAt(0), 0x45d9f3b);
  return domainColors[Math.abs(hash) % domainColors.length];
}

function evidenceColor(color: string, tier: string): string {
  if (tier === "generative_consensus") return color;
  if (tier === "deterministic_rule") return mix(color, "#ffffff", 0.14);
  if (tier === "source_contract") return mix(color, "#9ba7ba", 0.32);
  return mix(color, "#9ba7ba", 0.45);
}

function mix(left: string, right: string, amount: number): string {
  const a = hex(left);
  const b = hex(right);
  return `#${[0, 1, 2]
    .map((index) => Math.round(lerp(a[index], b[index], amount)).toString(16).padStart(2, "0"))
    .join("")}`;
}

function withAlpha(color: string, alpha: number): string {
  const [red, green, blue] = hex(color);
  return `rgba(${red}, ${green}, ${blue}, ${alpha.toFixed(3)})`;
}

function hex(color: string): [number, number, number] {
  const value = color.replace("#", "");
  return [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16)) as [number, number, number];
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function lerp(from: number, to: number, amount: number): number {
  return from + (to - from) * amount;
}
