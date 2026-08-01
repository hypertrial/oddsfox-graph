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

const relationColors = {
  dark: {
    implies: "#72a8ff",
    equivalent: "#55d2bd",
    complement: "#ff9184",
    mutually_exclusive: "#c6a1ff",
    compatible: "#9cacc2",
  },
  light: {
    implies: "#245eae",
    equivalent: "#087566",
    complement: "#ad423c",
    mutually_exclusive: "#7248a2",
    compatible: "#596779",
  },
} as const;

const domainColors = {
  dark: [
    "#72b5ff",
    "#58d1b2",
    "#ffc36c",
    "#e7a0e5",
    "#ff8999",
    "#a9baff",
    "#b7d97e",
    "#e5b77c",
  ],
  light: [
    "#246ca6",
    "#0b755f",
    "#985900",
    "#893f89",
    "#ad3553",
    "#465fa2",
    "#58751f",
    "#80551c",
  ],
} as const;

interface RenderState {
  selectedId: string | null;
  hoveredId: string | null;
  story: StoryFrameState | null;
  persistentLabels: ReadonlyMap<string, string>;
  darkMode: boolean;
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
    persistentLabels: new Map(),
    darkMode: false,
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
    renderState.current.darkMode = colorScheme.matches;
    const renderer = new Sigma(graph, container.current, {
      allowInvalidContainer: true,
      renderEdgeLabels: false,
      stagePadding: graphStagePadding(container.current),
      minEdgeThickness: 0.6,
      zIndex: true,
      labelSize: 13,
      labelWeight: "600",
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
      renderState.current.darkMode = colorScheme.matches;
      renderer.setSetting("labelColor", {
        color: colorScheme.matches ? "#dce5f2" : "#243044",
      });
      renderer.scheduleRefresh();
    };
    const updateStagePadding = () => {
      if (container.current) renderer.setSetting("stagePadding", graphStagePadding(container.current));
    };
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(updateStagePadding);
    resizeObserver?.observe(container.current);
    colorScheme.addEventListener("change", updateLabelColor);
    window.addEventListener("resize", updateStagePadding);
    return () => {
      activeView.current += 1;
      colorScheme.removeEventListener("change", updateLabelColor);
      window.removeEventListener("resize", updateStagePadding);
      resizeObserver?.disconnect();
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
    renderState.current.persistentLabels = persistentLabels(view);
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
      aria-label={`Interactive World Cup logic graph with ${view?.nodes.length ?? 0} outcome${view?.nodes.length === 1 ? "" : "s"} and ${view?.edges.length ?? 0} connection${view?.edges.length === 1 ? "" : "s"}. Use the search controls or point to an outcome to inspect nearby logic.`}
    />
  );
}

export function buildGraph(view: GraphView): Graph {
  const graph = new Graph({ multi: true, type: "directed" });
  const visibleDegrees = visibleDegreeByNode(view);
  const relations = new Set(view.edges.map((edge) => edge.relation));
  for (const node of view.nodes) {
    const visibleDegree = visibleDegrees.get(node.id) ?? 0;
    const degreeValue = view.level === "component" ? node.proposition_count : visibleDegree;
    const size = nodeSize(view.level, degreeValue, node.progression_outcome);
    const paletteKey = node.domain ?? node.component_id ?? node.id;
    graph.addNode(node.id, {
      label: node.label,
      x: node.x,
      y: node.y,
      size,
      color: domainColor(paletteKey, false),
      lightColor: domainColor(paletteKey, false),
      darkColor: domainColor(paletteKey, true),
      progressionOutcome: node.progression_outcome,
      visibleDegree,
    });
  }
  for (const edge of view.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    const lightColor = evidenceColor(relationColors.light[edge.relation], edge.evidence_tier, false);
    const darkColor = evidenceColor(relationColors.dark[edge.relation], edge.evidence_tier, true);
    const size = edgeSize(edge.relation, edge.count);
    graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
      color: lightColor,
      lightColor,
      darkColor,
      size,
      baseSize: size,
      type: edge.relation === "implies" ? "curvedArrow" : "curved",
      label: `${edge.relation.replaceAll("_", " ")} · ${edge.count}`,
      confidence: edge.confidence,
      relation: edge.relation,
      overviewOpacity: overviewEdgeOpacity(edge.relation, relations.size === 1),
    });
  }
  indexParallelEdgesIndex(graph);
  const yExtent = graphExtent(graph, "y");
  graph.forEachEdge((edge, attributes) => {
    const parallel = typeof attributes.parallelIndex === "number" ? attributes.parallelIndex : 0;
    const relation = String(attributes.relation);
    if (relation === "mutually_exclusive") {
      const [source, target] = graph.extremities(edge);
      const span = Math.abs(
        Number(graph.getNodeAttribute(source, "y")) - Number(graph.getNodeAttribute(target, "y")),
      );
      const normalizedSpan = yExtent === 0 ? 0 : span / yExtent;
      const direction = stableHash([source, target].sort().join("|")) % 2 === 0 ? -1 : 1;
      graph.setEdgeAttribute(edge, "curvature", direction * (0.14 + normalizedSpan * 0.34));
      return;
    }
    const relationCurve = relation === "complement" ? 0.16 : relation === "equivalent" ? 0.11 : 0.06;
    const parallelCurve = parallel === 0 ? relationCurve : Math.sign(parallel) * (relationCurve + Math.abs(parallel) * 0.14);
    graph.setEdgeAttribute(edge, "curvature", parallelCurve);
  });
  return graph;
}

function reduceNode(graph: Graph, state: RenderState, node: string, data: Record<string, unknown>) {
  if (state.story && !state.story.visibleNodes.has(node)) return { ...data, hidden: true };
  const focus = focusedNodes(graph, state);
  const relevant = focus.size === 0 || focus.has(node) || [...focus].some((id) => graph.hasNode(id) && graph.areNeighbors(node, id));
  const focused = focus.has(node);
  const persistentLabel = focus.size === 0 || relevant ? state.persistentLabels.get(node) : undefined;
  const forced = focused || persistentLabel !== undefined;
  const storyEndpoint = Boolean(state.story?.highlightedNodes.has(node));
  const baseColor = String(state.darkMode ? data.darkColor : data.lightColor);
  const progressionOutcome = data.progressionOutcome;
  const opacity = !relevant
    ? 0.1
    : focused || storyEndpoint
      ? 1
      : progressionOutcome === false
        ? 0.8
        : 0.94;
  return {
    ...data,
    label: focused ? String(data.label ?? node) : persistentLabel ?? null,
    forceLabel: forced,
    highlighted: focused && !state.story,
    size: storyEndpoint
      ? Number(data.size ?? 1) * (1 + 0.35 * (state.story?.reveal ?? 0))
      : Number(data.size ?? 1),
    color: withAlpha(baseColor, opacity),
    zIndex: focused ? 3 : persistentLabel ? 2 : relevant ? 1 : 0,
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
  const selected = state.selectedId === edge;
  const confidence = Number(data.confidence ?? 1);
  const baseSize = Number(data.baseSize ?? 1);
  const hasFocus = focus.size > 0 || Boolean(storyEdge);
  const alpha = emphasized || selected
    ? clamp(confidence, 0.82, 1)
    : hasFocus
      ? relevant
        ? clamp(confidence * 0.72, 0.48, 0.82)
        : 0.025
      : Number(data.overviewOpacity ?? 0.16) * clamp(confidence, 0.4, 1);
  const baseColor = String(state.darkMode ? data.darkColor : data.lightColor);
  return {
    ...data,
    color: withAlpha(baseColor, alpha),
    size: emphasized
      ? baseSize * (1 + 2.2 * (state.story?.emphasis ?? 0))
      : selected
        ? baseSize * 1.7
        : hasFocus && relevant
          ? baseSize * 1.2
          : baseSize,
    zIndex: emphasized || selected ? 4 : relevant ? 1 : 0,
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

function visibleDegreeByNode(view: GraphView): Map<string, number> {
  const degrees = new Map(view.nodes.map((node) => [node.id, 0]));
  for (const edge of view.edges) {
    if (degrees.has(edge.source)) degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1);
    if (degrees.has(edge.target)) degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1);
  }
  return degrees;
}

export function persistentLabels(view: GraphView): ReadonlyMap<string, string> {
  const degrees = visibleDegreeByNode(view);
  if (view.level !== "proposition") {
    return new Map(
      [...view.nodes]
        .sort(
          (left, right) =>
            (degrees.get(right.id) ?? 0) - (degrees.get(left.id) ?? 0) ||
            left.id.localeCompare(right.id),
        )
        .slice(0, 12)
        .map((node) => [node.id, node.label]),
    );
  }

  const anchors = new Map<string, GraphView["nodes"][number]>();
  for (const node of view.nodes) {
    if (!node.domain) continue;
    const current = anchors.get(node.domain);
    if (!current || compareTeamAnchor(node, current) < 0) anchors.set(node.domain, node);
  }
  if (anchors.size > 0) {
    return new Map([...anchors.entries()].map(([team, node]) => [node.id, team]));
  }

  return new Map(
    [...view.nodes]
      .sort(
        (left, right) =>
          (degrees.get(right.id) ?? 0) - (degrees.get(left.id) ?? 0) ||
          left.id.localeCompare(right.id),
      )
      .slice(0, 12)
      .map((node) => [node.id, node.label]),
  );
}

function compareTeamAnchor(
  left: GraphView["nodes"][number],
  right: GraphView["nodes"][number],
): number {
  const progression = Number(right.progression_outcome === true) - Number(left.progression_outcome === true);
  if (progression !== 0) return progression;
  if (left.x !== right.x) return right.x - left.x;
  const close = (right.market_close_epoch ?? 0) - (left.market_close_epoch ?? 0);
  return close || left.id.localeCompare(right.id);
}

function nodeSize(
  level: GraphView["level"],
  degree: number,
  progressionOutcome: boolean | null | undefined,
): number {
  if (level === "proposition") {
    return progressionOutcome === false
      ? clamp(3.4 + Math.sqrt(degree) * 0.36, 3.4, 5.4)
      : clamp(4.3 + Math.sqrt(degree) * 0.5, 4.3, 8);
  }
  const bounds = level === "event" ? [7, 22] : [10, 30];
  return clamp(Math.sqrt(Math.max(1, degree)) * 3, bounds[0], bounds[1]);
}

function edgeSize(relation: string, count: number): number {
  const weight = clamp(Math.log2(count + 1) * 1.15, 0.8, 4);
  const relationWeight = relation === "equivalent"
    ? 1.35
    : relation === "complement"
      ? 1.15
      : relation === "mutually_exclusive" || relation === "compatible"
        ? 0.72
        : 1;
  return weight * relationWeight;
}

function overviewEdgeOpacity(relation: string, onlyRelation: boolean): number {
  if (relation === "mutually_exclusive") return onlyRelation ? 0.15 : 0.045;
  if (relation === "compatible") return onlyRelation ? 0.2 : 0.06;
  if (relation === "complement") return onlyRelation ? 0.42 : 0.16;
  if (relation === "equivalent") return onlyRelation ? 0.38 : 0.18;
  return onlyRelation ? 0.3 : 0.14;
}

function graphExtent(graph: Graph, attribute: string): number {
  const values = graph.mapNodes((_node, attributes) => Number(attributes[attribute]));
  return values.length ? Math.max(...values) - Math.min(...values) : 0;
}

function graphStagePadding(element: HTMLElement): number {
  return clamp(element.clientWidth * 0.22, 58, 100);
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

function domainColor(domain: string, darkMode: boolean): string {
  let hash = 0;
  for (const character of domain) hash = Math.imul(hash ^ character.charCodeAt(0), 0x45d9f3b);
  const colors = darkMode ? domainColors.dark : domainColors.light;
  return colors[Math.abs(hash) % colors.length];
}

function evidenceColor(color: string, tier: string, darkMode: boolean): string {
  if (tier === "generative_consensus") return color;
  const neutral = darkMode ? "#a3afc1" : "#536176";
  if (tier === "deterministic_rule") return mix(color, neutral, 0.1);
  if (tier === "source_contract") return mix(color, neutral, 0.28);
  return mix(color, neutral, 0.42);
}

function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
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
