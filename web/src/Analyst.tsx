import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { safeClaim } from "./human";
import type {
  EvidenceTier,
  GraphMetadata,
  GraphView,
  Relation,
  SearchNode,
} from "./types";

const relations: Array<Relation | "all"> = [
  "all",
  "implies",
  "equivalent",
  "complement",
  "mutually_exclusive",
  "compatible",
];

const relationLabels: Record<Relation | "all", string> = {
  all: "All logic + cross-team links",
  implies: "Progression paths",
  equivalent: "Same outcome",
  complement: "Yes / no pairs",
  mutually_exclusive: "Cross-team winner links",
  compatible: "Can coexist",
};

interface Props {
  metadata: GraphMetadata;
  onEnterStory: (confidence: number) => void;
}

export function Analyst({ metadata, onEnterStory }: Props) {
  const [view, setView] = useState<GraphView | null>(null);
  const [relation, setRelation] = useState<Relation | "all">("all");
  const [minConfidence, setMinConfidence] = useState(0.95);
  const [evidenceTier, setEvidenceTier] = useState<EvidenceTier>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reasonFrom, setReasonFrom] = useState("");
  const [reasonTo, setReasonTo] = useState("");
  const [reasonRelation, setReasonRelation] = useState<Relation>("implies");
  const [reasoning, setReasoning] = useState<unknown>(null);
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [contextLabel, setContextLabel] = useState("All teams");
  const overviewRequest = useRef<AbortController | null>(null);
  const graphFingerprint = String(metadata.viewer.graph_content_fingerprint ?? "unknown");
  const requestedFilterKey = `proposition:${relation}:${minConfidence.toFixed(2)}:${evidenceTier}:essential`;
  const [viewFilterKey, setViewFilterKey] = useState(requestedFilterKey);
  const isStatic = metadata.viewer.static === true;

  const loadOverview = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.overview(
        "proposition",
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        signal,
        "essential",
      );
      setView(next);
      setViewFilterKey(requestedFilterKey);
      setSelectedId(null);
      setDetail(null);
      setContextLabel("All teams");
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(message(reason));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [relation, minConfidence, evidenceTier, requestedFilterKey]);

  useEffect(() => {
    overviewRequest.current?.abort();
    const controller = new AbortController();
    overviewRequest.current = controller;
    const timer = window.setTimeout(() => void loadOverview(controller.signal), 150);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [loadOverview]);

  const selectNode = useCallback(async (id: string) => {
    setSelectedId(id);
    setError(null);
    try {
      const selected = view?.nodes.find((node) => node.id === id);
      if (selected?.level === "proposition") {
        if (!reasonFrom) setReasonFrom(id);
        else if (!reasonTo && reasonFrom !== id) setReasonTo(id);
      }
      if (selected?.level === "event") setDetail(await api.eventDetail(id));
      else if (selected?.level === "component") setDetail(await api.componentDetail(id));
      else setDetail(await api.nodeDetail(id));
    } catch (reason) {
      setError(message(reason));
    }
  }, [view, reasonFrom, reasonTo]);

  const selectEdge = useCallback(async (id: string) => {
    setSelectedId(id);
    if (id.startsWith("event:") || id.startsWith("component:")) {
      const edge = view?.edges.find((candidate) => candidate.id === id);
      setDetail(edge ? { aggregate: edge } : null);
      return;
    }
    try {
      setDetail(await api.edgeDetail(id));
    } catch (reason) {
      setError(message(reason));
    }
  }, [view]);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    try {
      setResults(await api.search(query.trim()));
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function openResult(result: SearchNode) {
    overviewRequest.current?.abort();
    setLoading(true);
    try {
      const next = await api.neighborhood(result.node_id, 1);
      const selected = next.nodes.find((node) => node.id === result.node_id);
      setView(api.filterGraphView(
        next,
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        selected?.progression_outcome !== false,
      ));
      setViewFilterKey(`search:${result.node_id}:essential`);
      setContextLabel(selected?.label ?? safeClaim(result.canonical_proposition));
      setSelectedId(result.node_id);
      if (!reasonFrom) setReasonFrom(result.node_id);
      else if (!reasonTo && reasonFrom !== result.node_id) setReasonTo(result.node_id);
      setResults([]);
      setDetail(await api.nodeDetail(result.node_id));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }

  async function focusSelection() {
    if (!selectedId || !view?.nodes.some((node) => node.id === selectedId)) return;
    setLoading(true);
    try {
      const next = await api.neighborhood(selectedId, 1);
      const selected = next.nodes.find((node) => node.id === selectedId);
      setView(api.filterGraphView(
        next,
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        selected?.progression_outcome !== false,
      ));
      setViewFilterKey(`focus:${selectedId}:essential`);
      setContextLabel("Focused selection");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }

  async function runProof() {
    if (!reasonFrom.trim() || !reasonTo.trim()) return;
    try {
      setReasoning(await api.prove(reasonFrom.trim(), reasonTo.trim()));
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function runWhyNot() {
    if (!reasonFrom.trim() || !reasonTo.trim()) return;
    try {
      setReasoning(await api.whyNot(reasonFrom.trim(), reasonTo.trim(), reasonRelation));
    } catch (reason) {
      setError(message(reason));
    }
  }

  const selectedNode = view?.nodes.find((node) => node.id === selectedId);
  const selectedEdge = view?.edges.find((edge) => edge.id === selectedId);
  const selectionTitle = selectedNode?.label
    ?? (selectedEdge ? relationLabels[selectedEdge.relation] : "Selected connection");
  const usesCloseTimeLayout = view?.layout_mode === "close_time";
  const visibleTeamCount = new Set(
    view?.nodes.map((node) => node.domain).filter((domain): domain is string => Boolean(domain)),
  ).size;

  return (
    <section className="analyst-page" aria-labelledby="analyst-heading">
      <section className="graph-toolbar" aria-label="Graph controls">
        <h1 id="analyst-heading" className="sr-only">World Cup logic graph</h1>
        <form className="search" onSubmit={submitSearch}>
          <label htmlFor="graph-search">Find a team or outcome</label>
          <div>
            <input id="graph-search" value={query} onChange={(event) => setQuery(event.target.value)} />
            <button type="submit">Find</button>
          </div>
          {results.length > 0 && (
            <ul className="search-results">
              {results.map((result) => (
                <li key={result.node_id}>
                  <button type="button" onClick={() => void openResult(result)}>
                    <span>{result.plain_claim ?? view?.nodes.find((node) => node.id === result.node_id)?.label ?? safeClaim(result.canonical_proposition)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </form>
        <label className="relation-filter">
          Show
          <select value={relation} onChange={(event) => setRelation(event.target.value as Relation | "all")}>
            {relations.filter((value) => value !== "compatible").map((value) => <option key={value} value={value}>{relationLabels[value]}</option>)}
          </select>
        </label>
        <span className="graph-summary">
          {contextLabel} · {visibleTeamCount} team{visibleTeamCount === 1 ? "" : "s"} · {view?.nodes.length ?? 0} outcomes · {view?.edges.length ?? 0} links
        </span>
        <button type="button" className="secondary reset-graph" onClick={() => void loadOverview()}>Reset</button>
        <details className="graph-options">
          <summary>More</summary>
          <div>
            <label>
              Evidence
              <select value={evidenceTier} onChange={(event) => setEvidenceTier(event.target.value as EvidenceTier)}>
                <option value="all">All evidence</option>
                <option value="source_contract">Defined by the market</option>
                <option value="deterministic_rule">Proven by a logic rule</option>
                <option value="generative_consensus">Supported by model checks</option>
              </select>
            </label>
            <label>
              Minimum confidence <strong>{Math.round(minConfidence * 100)}%</strong>
              <input type="range" min="0" max="1" step="0.01" value={minConfidence} onChange={(event) => setMinConfidence(Number(event.target.value))} />
            </label>
            <div className="toolbar-actions">
              <button type="button" className="secondary" onClick={() => setLayoutNonce((value) => value + 1)}>Re-layout</button>
              {!isStatic && <button type="button" onClick={() => onEnterStory(minConfidence)}>Auto story</button>}
            </div>
          </div>
        </details>
      </section>

      {error && <div className="error" role="alert">{error}</div>}
      {(view?.truncated_nodes || view?.truncated_edges) && <div className="notice">This view is bounded. Search or filter to inspect omitted nodes and edges.</div>}

      <section className={`workspace${selectedId ? " has-inspector" : ""}`}>
        <div className="graph-panel">
          {loading && <div className="loading">Loading graph…</div>}
          <GraphCanvas
            view={view}
            selectedId={selectedId}
            graphFingerprint={graphFingerprint}
            filterKey={viewFilterKey}
            layoutNonce={layoutNonce}
            story={null}
            frame={0}
            onSelectNode={selectNode}
            onSelectEdge={selectEdge}
          />
          {usesCloseTimeLayout && (
            <div className="timeline-guide" aria-hidden="true"><span>Earlier market close</span><span>Later market close →</span></div>
          )}
          <div className="legend" aria-label="Relation legend">
            {relation === "all"
              ? relations.slice(1, 5).map((value) => <span key={value}><i data-relation={value} />{relationLabels[value]}</span>)
              : <span><i data-relation={relation} />{relationLabels[relation]}</span>}
          </div>
        </div>
        {selectedId && (
          <aside className="inspector" aria-live="polite">
            <header className="inspector-header">
              <div><small>Selected</small><h2>{selectionTitle}</h2></div>
              <button type="button" className="close-inspector secondary" aria-label="Close selection" onClick={() => { setSelectedId(null); setDetail(null); }}>×</button>
            </header>
            {selectedNode && (
              <button type="button" className="focus-selection" onClick={() => void focusSelection()}>Show nearby logic only</button>
            )}
            {detail && (
              <details className="technical-details">
                <summary>Technical details</summary>
                <p className="selection-id">ID: {selectedId}</p>
                <pre>{JSON.stringify(detail, null, 2)}</pre>
              </details>
            )}
            {!isStatic && (
              <details className="reasoning">
                <summary>Proof tools</summary>
                <div>
                  <label>From outcome ID<input value={reasonFrom} onChange={(event) => setReasonFrom(event.target.value)} /></label>
                  <label>To outcome ID<input value={reasonTo} onChange={(event) => setReasonTo(event.target.value)} /></label>
                  <label>
                    Missing relationship
                    <select value={reasonRelation} onChange={(event) => setReasonRelation(event.target.value as Relation)}>
                      {relations.slice(1).map((value) => <option key={value} value={value}>{relationLabels[value]}</option>)}
                    </select>
                  </label>
                  <div className="reason-actions">
                    <button type="button" onClick={() => void runProof()}>Prove path</button>
                    <button type="button" className="secondary" onClick={() => void runWhyNot()}>Explain absence</button>
                  </div>
                  {reasoning !== null && <pre>{JSON.stringify(reasoning, null, 2)}</pre>}
                </div>
              </details>
            )}
          </aside>
        )}
      </section>
    </section>
  );
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
