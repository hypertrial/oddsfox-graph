import { useEffect, useRef } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import type { GraphView } from "./types";

const relationColors: Record<string, string> = {
  implies: "#4f8cff",
  equivalent: "#2ab7a9",
  complement: "#e06b65",
  mutually_exclusive: "#c284e8",
  compatible: "#8b97a6",
};

interface Props {
  view: GraphView | null;
  selectedId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
}

export function GraphCanvas({ view, selectedId, onSelectNode, onSelectEdge }: Props) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current || !view) return undefined;
    const graph = new Graph({ multi: true, type: "directed" });
    for (const node of view.nodes) {
      graph.addNode(node.id, {
        label: node.label,
        x: node.x,
        y: node.y,
        size: Math.max(3, Math.min(18, node.size)),
        color: node.id === selectedId ? "#ffb454" : "#5d8fd8",
        forceLabel: node.id === selectedId,
      });
    }
    for (const edge of view.edges) {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
        color: relationColors[edge.relation],
        size: Math.max(0.6, Math.min(4, Math.log2(edge.count + 1))),
        type: edge.relation === "implies" ? "arrow" : "line",
        label: `${edge.relation} · ${edge.count}`,
      });
    }
    const renderer = new Sigma(graph, container.current, {
      allowInvalidContainer: false,
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 9,
      defaultEdgeType: "line",
      enableEdgeEvents: true,
    });
    renderer.on("clickNode", ({ node }) => onSelectNode(node));
    renderer.on("clickEdge", ({ edge }) => onSelectEdge(edge));
    return () => renderer.kill();
  }, [view, selectedId, onSelectNode, onSelectEdge]);

  return (
    <div
      className="graph-canvas"
      ref={container}
      role="img"
      aria-label={`Interactive logic graph with ${view?.nodes.length ?? 0} nodes and ${view?.edges.length ?? 0} relations. Use search to select a proposition without a pointer.`}
    />
  );
}
