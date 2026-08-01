import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { coverageLabel } from "./human";
import type {
  EvidenceTier,
  ExplorerLevel,
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

interface Props {
  metadata: GraphMetadata;
  onEnterStory: (confidence: number) => void;
}

export function Analyst({ metadata, onEnterStory }: Props) {
  const [view, setView] = useState<GraphView | null>(null);
  const [level, setLevel] = useState<"component" | "event">("event");
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
  const [breadcrumbs, setBreadcrumbs] = useState<string[]>(["Analyst graph"]);
  const overviewRequest = useRef<AbortController | null>(null);
  const graphFingerprint = String(metadata.viewer.graph_content_fingerprint ?? "unknown");
  const requestedFilterKey = `${level}:${relation}:${minConfidence.toFixed(2)}:${evidenceTier}:all`;
  const [viewFilterKey, setViewFilterKey] = useState(requestedFilterKey);
  const isStatic = metadata.viewer.static === true;

  const loadOverview = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.overview(
        level,
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        signal,
        "all",
      );
      setView(next);
      setViewFilterKey(requestedFilterKey);
      setSelectedId(null);
      setDetail(null);
      setBreadcrumbs(["Analyst graph", level === "component" ? "Components" : "Events"]);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(message(reason));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [level, relation, minConfidence, evidenceTier, requestedFilterKey]);

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

  const openNode = useCallback(async (id: string) => {
    const selected = view?.nodes.find((node) => node.id === id);
    if (!selected || selected.level === "proposition") return;
    overviewRequest.current?.abort();
    setLoading(true);
    setError(null);
    try {
      if (selected.level === "event") {
        setView(await api.eventGraph(id, relation, minConfidence, evidenceTier));
        setViewFilterKey(`event:${id}:${requestedFilterKey}`);
        setBreadcrumbs(["Analyst graph", "Events", selected.label]);
      } else {
        setView(await api.componentGraph(id, relation, minConfidence, evidenceTier));
        setViewFilterKey(`component:${id}:${requestedFilterKey}`);
        setBreadcrumbs(["Analyst graph", selected.label, "Events"]);
      }
      setSelectedId(null);
      setDetail(null);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }, [view, relation, minConfidence, evidenceTier, requestedFilterKey]);

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
      const next = await api.neighborhood(result.node_id);
      setView(next);
      setViewFilterKey(`search:${result.node_id}:all`);
      setBreadcrumbs(["Analyst graph", "Search", result.canonical_proposition]);
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
      setView(await api.neighborhood(selectedId, 1));
      setViewFilterKey(`focus:${selectedId}:all`);
      setBreadcrumbs(["Analyst graph", "Focused selection"]);
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

  const dense = useMemo(() => {
    const density = view && view.nodes.length > 1
      ? view.edges.length / (view.nodes.length * (view.nodes.length - 1))
      : 0;
    return Boolean(
      view && (view.nodes.length > 200 || view.edges.length > 500 || density > 0.1),
    );
  }, [view]);
  const coverage = coverageLabel(
    String(metadata.coverage.classification_status ?? ""),
    metadata.coverage.classification_coverage,
  );

  return (
    <section className="analyst-page" aria-labelledby="analyst-heading">
      <header className="page-heading analyst-heading">
        <div>
          <p className="eyebrow">Technical workspace</p>
          <h2 id="analyst-heading">Analyst graph</h2>
          <p>Inspect every published node and relationship. Double-click an event or component to drill in.</p>
        </div>
        <div className="analyst-health">
          <strong>{coverage}</strong>
          <span>{view?.nodes.length ?? 0} nodes · {view?.edges.length ?? 0} edges</span>
        </div>
      </header>

      <nav className="breadcrumbs" aria-label="Graph location">
        {breadcrumbs.map((crumb, index) => (
          <button key={`${crumb}-${index}`} type="button" disabled={index > 0} onClick={() => void loadOverview()}>{crumb}</button>
        ))}
      </nav>

      <section className="toolbar" aria-label="Graph controls">
        <form className="search" onSubmit={submitSearch}>
          <label htmlFor="graph-search">Search graph nodes</label>
          <div>
            <input id="graph-search" value={query} onChange={(event) => setQuery(event.target.value)} />
            <button type="submit">Search</button>
          </div>
          {results.length > 0 && (
            <ul className="search-results">
              {results.map((result) => (
                <li key={result.node_id}>
                  <button type="button" onClick={() => void openResult(result)}>
                    <span>{result.canonical_proposition}</span>
                    <small>{result.event_slug || result.market_id}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </form>
        <label>
          Level
          <select
            value={isStatic ? "static" : level}
            disabled={isStatic}
            title={isStatic ? "Static snapshots retain their exported graph level" : undefined}
            onChange={(event) => setLevel(event.target.value as "component" | "event")}
          >
            {isStatic && <option value="static">Snapshot {view?.level ?? "graph"}</option>}
            <option value="event">Events</option>
            <option value="component">Components</option>
          </select>
        </label>
        <label>
          Relation
          <select value={relation} onChange={(event) => setRelation(event.target.value as Relation | "all")}>
            {relations.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
          </select>
        </label>
        <label>
          Evidence
          <select value={evidenceTier} onChange={(event) => setEvidenceTier(event.target.value as EvidenceTier)}>
            <option value="all">all evidence</option>
            <option value="source_contract">source contract</option>
            <option value="deterministic_rule">deterministic rule</option>
            <option value="generative_consensus">generative consensus</option>
          </select>
        </label>
        <label>
          Minimum confidence <strong>{minConfidence.toFixed(2)}</strong>
          <input type="range" min="0" max="1" step="0.01" value={minConfidence} onChange={(event) => setMinConfidence(Number(event.target.value))} />
        </label>
        <div className="toolbar-actions">
          <button type="button" className="secondary" onClick={() => void loadOverview()}>Reset view</button>
          <button type="button" className="secondary" onClick={() => setLayoutNonce((value) => value + 1)}>Re-layout</button>
          {!isStatic && <button type="button" onClick={() => onEnterStory(minConfidence)}>Auto story</button>}
        </div>
      </section>

      {error && <div className="error" role="alert">{error}</div>}
      {(view?.truncated_nodes || view?.truncated_edges) && <div className="notice">This view is bounded. Search or filter to inspect omitted nodes and edges.</div>}
      {dense && (
        <div className="notice dense-notice">
          <span>This is a dense technical view. Select a node, then focus it to reduce visual overlap.</span>
          <button type="button" className="secondary" disabled={!selectedId || !view?.nodes.some((node) => node.id === selectedId)} onClick={() => void focusSelection()}>Focus selection</button>
        </div>
      )}

      <section className="workspace">
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
            onOpenNode={(id) => void openNode(id)}
          />
          <div className="legend" aria-label="Relation legend">
            {relations.slice(1).map((value) => <span key={value}><i data-relation={value} />{value.replaceAll("_", " ")}</span>)}
          </div>
        </div>
        <aside className="inspector" aria-live="polite">
          <h2>{selectedId ? "Selection" : "Graph run"}</h2>
          {detail ? (
            <details className="technical-details">
              <summary>Technical details</summary>
              <pre>{JSON.stringify(detail, null, 2)}</pre>
            </details>
          ) : (
            <dl>
              <div><dt>Visible nodes</dt><dd>{view?.nodes.length ?? 0}</dd></div>
              <div><dt>Visible relations</dt><dd>{view?.edges.length ?? 0}</dd></div>
              <div><dt>Level</dt><dd>{(view?.level as ExplorerLevel | undefined) ?? "—"}</dd></div>
              <div><dt>Graph version</dt><dd>{metadata.package_version}</dd></div>
            </dl>
          )}
          {selectedId && <p className="selection-id">ID: {selectedId}</p>}
          {!isStatic && (
            <section className="reasoning" aria-labelledby="reasoning-heading">
              <h3 id="reasoning-heading">Proof tools</h3>
              <label>From proposition ID<input value={reasonFrom} onChange={(event) => setReasonFrom(event.target.value)} /></label>
              <label>To proposition ID<input value={reasonTo} onChange={(event) => setReasonTo(event.target.value)} /></label>
              <label>
                Why-not relation
                <select value={reasonRelation} onChange={(event) => setReasonRelation(event.target.value as Relation)}>
                  {relations.slice(1).map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
                </select>
              </label>
              <div className="reason-actions">
                <button type="button" onClick={() => void runProof()}>Prove path</button>
                <button type="button" className="secondary" onClick={() => void runWhyNot()}>Explain absence</button>
              </div>
              {reasoning !== null && <pre>{JSON.stringify(reasoning, null, 2)}</pre>}
            </section>
          )}
          {isStatic && <p className="snapshot-note">Proof and why-not diagnostics require the original graph directory.</p>}
        </aside>
      </section>
    </section>
  );
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
