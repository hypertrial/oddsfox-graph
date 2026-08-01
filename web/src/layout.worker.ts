/// <reference lib="webworker" />

import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

interface LayoutGroupRequest {
  id: string;
  nodes: Array<{ id: string; x: number; y: number; size: number }>;
  edges: Array<{ id: string; source: string; target: string; weight: number }>;
}

interface LayoutRequest {
  key: string;
  groups: LayoutGroupRequest[];
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const results = event.data.groups.map((group) => {
    const graph = new Graph({ multi: true, type: "directed" });
    for (const node of group.nodes) graph.addNode(node.id, node);
    for (const edge of group.edges) {
      if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
        graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
          weight: edge.weight,
        });
      }
    }
    const settings = {
      ...forceAtlas2.inferSettings(graph),
      adjustSizes: true,
      barnesHutOptimize: group.nodes.length >= 100,
    };
    const positions = forceAtlas2(graph, {
      iterations: 250,
      settings,
      getEdgeWeight: "weight",
    });
    return {
      id: group.id,
      positions,
      settings,
      nodeCount: group.nodes.length,
    };
  });
  self.postMessage({ key: event.data.key, groups: results });
};

export {};
