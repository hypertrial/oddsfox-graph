import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const [searchState, setSearchState] = useState<"idle" | "pending" | "complete">("idle");
  const [moreOpen, setMoreOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reasonFrom, setReasonFrom] = useState("");
  const [reasonTo, setReasonTo] = useState("");
  const [reasonRelation, setReasonRelation] = useState<Relation>("implies");
  const [reasoning, setReasoning] = useState<unknown>(null);
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [contextLabel, setContextLabel] = useState("All teams");
  const overviewRequest = useRef<AbortController | null>(null);
  const detailRequest = useRef(0);
  const searchRequest = useRef(0);
  const searchInput = useRef<HTMLInputElement | null>(null);
  const inspectorClose = useRef<HTMLButtonElement | null>(null);
  const focusInspectorOnOpen = useRef(false);
  const graphFingerprint = String(metadata.viewer.graph_content_fingerprint ?? "unknown");
  const requestedFilterKey = `proposition:${relation}:${minConfidence.toFixed(2)}:${evidenceTier}:essential`;
  const [viewFilterKey, setViewFilterKey] = useState(requestedFilterKey);
  const isStatic = "static" in metadata.viewer && metadata.viewer.static === true;

  const loadOverview = useCallback(async (signal?: AbortSignal) => {
    detailRequest.current += 1;
    setLoading(true);
    setError(null);
    setSelectedId(null);
    setDetail(null);
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
      detailRequest.current += 1;
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
    const timer = window.setTimeout(() => {
      if (!controller.signal.aborted) void loadOverview(controller.signal);
    }, 150);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [loadOverview]);

  const selectNode = useCallback(async (id: string, focusInspector = false) => {
    const request = ++detailRequest.current;
    setSelectedId(id);
    setDetail(null);
    setError(null);
    if (focusInspector) focusInspectorOnOpen.current = true;
    try {
      const selected = view?.nodes.find((node) => node.id === id);
      if (selected?.level === "proposition") {
        if (!reasonFrom) setReasonFrom(id);
        else if (!reasonTo && reasonFrom !== id) setReasonTo(id);
      }
      const nextDetail = selected?.level === "event"
        ? await api.eventDetail(id)
        : selected?.level === "component"
          ? await api.componentDetail(id)
          : await api.nodeDetail(id);
      if (detailRequest.current === request) setDetail(nextDetail);
    } catch (reason) {
      if (detailRequest.current === request) setError(message(reason));
    }
  }, [view, reasonFrom, reasonTo]);

  const selectEdge = useCallback(async (id: string, focusInspector = false) => {
    const request = ++detailRequest.current;
    setSelectedId(id);
    setDetail(null);
    setError(null);
    if (focusInspector) focusInspectorOnOpen.current = true;
    if (id.startsWith("event:") || id.startsWith("component:")) {
      const edge = view?.edges.find((candidate) => candidate.id === id);
      if (detailRequest.current === request) setDetail(edge ? { aggregate: edge } : null);
      return;
    }
    try {
      const nextDetail = await api.edgeDetail(id);
      if (detailRequest.current === request) setDetail(nextDetail);
    } catch (reason) {
      if (detailRequest.current === request) setError(message(reason));
    }
  }, [view]);

  const closeSelection = useCallback(() => {
    detailRequest.current += 1;
    focusInspectorOnOpen.current = false;
    setSelectedId(null);
    setDetail(null);
    window.requestAnimationFrame(() => searchInput.current?.focus());
  }, []);

  useLayoutEffect(() => {
    if (!focusInspectorOnOpen.current || !inspectorClose.current) return;
    focusInspectorOnOpen.current = false;
    inspectorClose.current.focus();
  });

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (results.length > 0 || searchState !== "idle" || moreOpen) {
        searchRequest.current += 1;
        setResults([]);
        setSearchState("idle");
        setMoreOpen(false);
        searchInput.current?.focus();
        return;
      }
      if (selectedId) closeSelection();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [closeSelection, moreOpen, results.length, searchState, selectedId]);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    const searchQuery = query.trim();
    if (!searchQuery) {
      setResults([]);
      setSearchState("idle");
      return;
    }
    const request = ++searchRequest.current;
    setResults([]);
    setSearchState("pending");
    setMoreOpen(false);
    setError(null);
    try {
      const nextResults = await api.search(searchQuery);
      if (searchRequest.current !== request) return;
      setResults(nextResults);
      setSearchState("complete");
    } catch (reason) {
      if (searchRequest.current === request) {
        setSearchState("idle");
        setError(message(reason));
      }
    }
  }

  function updateQuery(value: string) {
    searchRequest.current += 1;
    setQuery(value);
    setResults([]);
    setSearchState("idle");
  }

  async function openResult(result: SearchNode) {
    const request = ++detailRequest.current;
    overviewRequest.current?.abort();
    setLoading(true);
    setDetail(null);
    setError(null);
    try {
      const next = await api.neighborhood(result.node_id, 1);
      if (detailRequest.current !== request) return;
      const selected = next.nodes.find((node) => node.id === result.node_id);
      const filtered = api.filterGraphView(
        next,
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        selected?.progression_outcome !== false,
      );
      setView(selected && !filtered.nodes.some((node) => node.id === selected.id)
        ? { ...filtered, nodes: [...filtered.nodes, selected] }
        : filtered);
      setViewFilterKey(`search:${result.node_id}:essential`);
      setContextLabel(selected?.label ?? safeClaim(result.canonical_proposition));
      focusInspectorOnOpen.current = true;
      setSelectedId(result.node_id);
      if (!reasonFrom) setReasonFrom(result.node_id);
      else if (!reasonTo && reasonFrom !== result.node_id) setReasonTo(result.node_id);
      setResults([]);
      setSearchState("idle");
      const nextDetail = await api.nodeDetail(result.node_id);
      if (detailRequest.current === request) setDetail(nextDetail);
    } catch (reason) {
      if (detailRequest.current === request) setError(message(reason));
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
      const filtered = api.filterGraphView(
        next,
        relation,
        minConfidence,
        relation === "compatible",
        evidenceTier,
        selected?.progression_outcome !== false,
      );
      setView(selected && !filtered.nodes.some((node) => node.id === selected.id)
        ? { ...filtered, nodes: [...filtered.nodes, selected] }
        : filtered);
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
  const selectedSource = view?.nodes.find((node) => node.id === selectedEdge?.source);
  const selectedTarget = view?.nodes.find((node) => node.id === selectedEdge?.target);
  const incidentEdges = selectedNode
    ? (view?.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id) ?? [])
    : [];
  const selectionTitle = selectedNode?.label
    ?? (selectedEdge ? relationLabels[selectedEdge.relation] : "Selected connection");
  const usesCloseTimeLayout = view?.layout_mode === "close_time";
  const usesProgressionLayout = view?.layout_mode === "progression";
  const visibleTeamCount = new Set(
    view?.nodes.map((node) => node.domain).filter((domain): domain is string => Boolean(domain)),
  ).size;

  return (
    <section className="analyst-page" aria-labelledby="analyst-heading">
      <section className="graph-toolbar" aria-label="Graph controls">
        <h1 id="analyst-heading" className="sr-only">World Cup logic graph</h1>
        <form className="search" onSubmit={submitSearch} role="search">
          <label htmlFor="graph-search">Find a team or outcome</label>
          <div>
            <input
              ref={searchInput}
              id="graph-search"
              value={query}
              aria-expanded={results.length > 0 || searchState !== "idle"}
              aria-controls="graph-search-results"
              aria-describedby="graph-search-status"
              onChange={(event) => updateQuery(event.target.value)}
            />
            <button type="submit">Find</button>
          </div>
          {results.length > 0 && (
            <ul id="graph-search-results" className="search-results" aria-label="Matching outcomes">
              {results.map((result) => (
                <li key={result.node_id}>
                  <button type="button" onClick={() => void openResult(result)}>
                    <span>{result.plain_claim ?? view?.nodes.find((node) => node.id === result.node_id)?.label ?? safeClaim(result.canonical_proposition)}</span>
                    <small>{result.outcome_label} market outcome</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div id="graph-search-status" className={`search-feedback${searchState === "idle" || results.length > 0 ? " sr-only" : ""}`} role="status" aria-live="polite">
            {searchState === "pending" && "Searching outcomes…"}
            {searchState === "complete" && results.length === 0 && `No outcomes found for “${query.trim()}”.`}
            {searchState === "complete" && results.length > 0 && `${results.length} matching outcomes.`}
          </div>
        </form>
        <label className="relation-filter">
          Show
          <select value={relation} onChange={(event) => setRelation(event.target.value as Relation | "all")}>
            {relations.filter((value) => value !== "compatible").map((value) => <option key={value} value={value}>{relationLabels[value]}</option>)}
          </select>
        </label>
        <span className="graph-summary" role="status" aria-live="polite">
          {contextLabel} · {visibleTeamCount} team{visibleTeamCount === 1 ? "" : "s"} · {view?.nodes.length ?? 0} outcomes · {view?.edges.length ?? 0} links
        </span>
        <button type="button" className="secondary reset-graph" onClick={() => void loadOverview()}>Reset</button>
        <details
          className="graph-options"
          open={moreOpen}
          onToggle={(event) => {
            const open = event.currentTarget.open;
            setMoreOpen(open);
            if (open) {
              searchRequest.current += 1;
              setResults([]);
              setSearchState("idle");
            }
          }}
        >
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
              {view?.layout_mode === "hierarchical" && (
                <button type="button" className="secondary" onClick={() => setLayoutNonce((value) => value + 1)}>Re-layout</button>
              )}
              {!isStatic && <button type="button" onClick={() => onEnterStory(minConfidence)}>Auto story</button>}
            </div>
          </div>
        </details>
      </section>

      {error && <div className="error" role="alert">{error}</div>}
      {(view?.truncated_nodes || view?.truncated_edges) && <div className="notice">This view is bounded. Search or filter to inspect omitted nodes and edges.</div>}

      <section className={`workspace${selectedId ? " has-inspector" : ""}`} aria-busy={loading}>
        <div className="graph-panel">
          {loading && <div className="loading" role="status" aria-live="polite">Loading graph…</div>}
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
            <div className="timeline-guide" role="note" aria-label="Timeline runs from earlier market close on the left to later market close on the right"><span>Earlier market close</span><span>Later market close →</span></div>
          )}
          {usesProgressionLayout && (
            <div className="timeline-guide" role="note" aria-label="Progression runs from the opening knockout stage on the left to World Cup winner on the right"><span>Round of 32</span><span>World Cup winner →</span></div>
          )}
          <div className="legend" aria-label="Relation legend">
            {relation === "all"
              ? relations.slice(1, 5).map((value) => <span key={value}><i data-relation={value} />{relationLabels[value]}</span>)
              : <span><i data-relation={relation} />{relationLabels[relation]}</span>}
          </div>
        </div>
        {selectedId && (
          <aside className="inspector" aria-live="polite" aria-labelledby="selection-heading">
            <header className="inspector-header">
              <div><small>Selected</small><h2 id="selection-heading">{selectionTitle}</h2></div>
              <button ref={inspectorClose} type="button" className="close-inspector secondary" aria-label="Close selection" onClick={closeSelection}>×</button>
            </header>
            {selectedNode && (
              <div className="selection-summary node-summary">
                <dl>
                  <div><dt>Team</dt><dd>{selectedNode.domain ?? "World Cup"}</dd></div>
                  <div><dt>Outcome</dt><dd>{outcomePolarity(selectedNode.progression_outcome)}</dd></div>
                  <div><dt>Visible links</dt><dd>{incidentEdges.length}</dd></div>
                  {selectedNode.market_close_epoch != null && <div><dt>Market closes</dt><dd>{formatCloseDate(selectedNode.market_close_epoch)}</dd></div>}
                </dl>
              </div>
            )}
            {selectedEdge && (
              <div className="selection-summary edge-summary">
                <p className="edge-claim"><small>From</small>{selectedSource?.label ?? selectedEdge.source}</p>
                <p className="relation-explanation">{relationExplanation(selectedEdge.relation)}</p>
                <p className="edge-claim"><small>To</small>{selectedTarget?.label ?? selectedEdge.target}</p>
                <div className="evidence-line">
                  <span>{Math.round(selectedEdge.confidence * 100)}% confidence</span>
                  <span>{evidenceLabel(selectedEdge.evidence_tier)}</span>
                </div>
              </div>
            )}
            {selectedNode && (
              <button type="button" className="focus-selection" onClick={() => void focusSelection()}>Show nearby logic only</button>
            )}
            {selectedNode && incidentEdges.length > 0 && (
              <section className="incident-section" aria-labelledby="incident-heading">
                <h3 id="incident-heading">Visible connections</h3>
                <ul className="incident-connections">
                  {incidentEdges.map((edge) => {
                    const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                    const other = view?.nodes.find((node) => node.id === otherId);
                    return (
                      <li key={edge.id}>
                        <button type="button" className="secondary" onClick={() => void selectEdge(edge.id, true)}>
                          <span>{relationLabels[edge.relation]}</span>
                          <small>{other?.label ?? otherId}</small>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
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

function outcomePolarity(progressionOutcome: boolean | null | undefined): string {
  if (progressionOutcome === true) return "Team progresses";
  if (progressionOutcome === false) return "Team does not progress";
  return "Market outcome";
}

function formatCloseDate(epoch: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(epoch * 1000));
}

function relationExplanation(relation: Relation): string {
  if (relation === "implies") return "If the first outcome happens, the second must also have happened.";
  if (relation === "equivalent") return "These outcomes describe the same progression result.";
  if (relation === "complement") return "Exactly one answer can be true for this market.";
  if (relation === "mutually_exclusive") return "These teams cannot both win the tournament.";
  return "These outcomes can happen together.";
}

function evidenceLabel(tier: string): string {
  if (tier === "source_contract") return "Defined by the market";
  if (tier === "deterministic_rule") return "Proven by a logic rule";
  if (tier === "generative_consensus") return "Supported by model checks";
  return "Published evidence";
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
