import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import { GraphCanvas } from "./GraphCanvas";
import type { EvidenceTier, ExplorerLevel, GraphMetadata, GraphView, Relation, SearchNode } from "./types";

const relations: Array<Relation | "all"> = [
  "all",
  "implies",
  "equivalent",
  "complement",
  "mutually_exclusive",
  "compatible",
];

export function App() {
  const [metadata, setMetadata] = useState<GraphMetadata | null>(null);
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

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.overview(level, relation, minConfidence, relation === "compatible", evidenceTier);
      setView(next);
      setSelectedId(null);
      setDetail(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [level, relation, minConfidence, evidenceTier]);

  useEffect(() => {
    if (metadata !== null) return;
    void api.metadata().then(setMetadata).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
      setLoading(false);
    });
  }, [metadata]);

  useEffect(() => {
    if (metadata === null) return;
    void loadOverview();
  }, [metadata, loadOverview]);

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
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [view, reasonFrom, reasonTo]);

  const selectEdge = useCallback(async (id: string) => {
    if (id.startsWith("event:") || id.startsWith("component:")) {
      const edge = view?.edges.find((candidate) => candidate.id === id);
      setDetail(edge ? { aggregate: edge } : null);
      return;
    }
    try {
      setDetail(await api.edgeDetail(id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [view]);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    try {
      setResults(await api.search(query.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function openResult(result: SearchNode) {
    setLoading(true);
    try {
      const next = await api.neighborhood(result.node_id);
      setView(next);
      setSelectedId(result.node_id);
      if (!reasonFrom) setReasonFrom(result.node_id);
      else if (!reasonTo && reasonFrom !== result.node_id) setReasonTo(result.node_id);
      setResults([]);
      setDetail(await api.nodeDetail(result.node_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function runProof() {
    if (!reasonFrom.trim() || !reasonTo.trim()) return;
    try {
      setReasoning(await api.prove(reasonFrom.trim(), reasonTo.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function runWhyNot() {
    if (!reasonFrom.trim() || !reasonTo.trim()) return;
    try {
      setReasoning(await api.whyNot(reasonFrom.trim(), reasonTo.trim(), reasonRelation));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const coverage = useMemo(() => {
    const value = metadata?.coverage.classification_coverage;
    return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
  }, [metadata]);
  const coverageGap = metadata?.coverage.classification_gap;
  const inputSelection = metadata?.coverage.input_selection;
  const inputMarketRows =
    typeof inputSelection === "object" && inputSelection !== null
      ? (inputSelection as Record<string, unknown>).input_market_rows
      : undefined;
  const isStatic = metadata?.viewer.static === true;
  const buildMode = String(metadata?.build.build_mode ?? metadata?.viewer.build_mode ?? "unknown");
  const validationStatus = String(metadata?.build.validation_status ?? metadata?.viewer.validation_status ?? "unknown");

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">OddsFox</p>
          <h1>Logic Explorer</h1>
        </div>
        <div className="run-health" aria-label="Graph coverage">
          <span className={`mode-badge mode-${buildMode}`}>{buildMode} · {validationStatus.replaceAll("_", " ")}</span>
          <span>Classification coverage</span>
          <strong>{coverage}</strong>
        </div>
      </header>

      <section className="toolbar" aria-label="Graph controls">
        <form className="search" onSubmit={submitSearch}>
          <label htmlFor="graph-search">Search markets and propositions</label>
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
            title={isStatic ? "Static exports retain their exported graph level" : undefined}
            onChange={(event) => setLevel(event.target.value as "component" | "event")}
          >
            {isStatic && (
              <option value="static">Exported {view?.level ?? "graph"}</option>
            )}
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
        <button type="button" className="secondary" onClick={() => void loadOverview()}>Reset view</button>
      </section>

      {error && <div className="error" role="alert">{error}</div>}
      {(view?.truncated_nodes || view?.truncated_edges) && (
        <div className="notice">This view is bounded. Search or filter to inspect omitted nodes and edges.</div>
      )}
      {typeof coverageGap === "number" && coverageGap > 0 && (
        <div className="notice">
          {(coverageGap * 100).toFixed(1)}% of eligible retrieved pairs remain unclassified; all selected propositions are still searchable.
        </div>
      )}

      <section className="workspace">
        <div className="graph-panel">
          {loading && <div className="loading">Loading graph…</div>}
          <GraphCanvas view={view} selectedId={selectedId} onSelectNode={selectNode} onSelectEdge={selectEdge} />
          <div className="legend" aria-label="Relation legend">
            {relations.slice(1).map((value) => <span key={value}><i data-relation={value} />{value.replaceAll("_", " ")}</span>)}
          </div>
        </div>
        <aside className="inspector" aria-live="polite">
          <h2>{selectedId ? "Selection" : "Graph run"}</h2>
          <p className="selection-id">{selectedId ?? `v${metadata?.package_version ?? "…"}`}</p>
          {detail ? <pre>{JSON.stringify(detail, null, 2)}</pre> : (
            <dl>
              <div><dt>Visible nodes</dt><dd>{view?.nodes.length ?? 0}</dd></div>
              <div><dt>Visible relations</dt><dd>{view?.edges.length ?? 0}</dd></div>
              <div><dt>Level</dt><dd>{(view?.level as ExplorerLevel | undefined) ?? "—"}</dd></div>
              <div><dt>All markets selected</dt><dd>{String(metadata?.coverage.all_market_selection ?? "—")}</dd></div>
              <div><dt>Input market rows</dt><dd>{String(inputMarketRows ?? "—")}</dd></div>
            </dl>
          )}
          <section className="reasoning" aria-labelledby="reasoning-heading">
            <h3 id="reasoning-heading">Reasoning</h3>
            <label>
              From proposition
              <input value={reasonFrom} onChange={(event) => setReasonFrom(event.target.value)} />
            </label>
            <label>
              To proposition
              <input value={reasonTo} onChange={(event) => setReasonTo(event.target.value)} />
            </label>
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
        </aside>
      </section>
    </main>
  );
}
