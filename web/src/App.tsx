import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import * as api from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { freezeLayout } from "./layout";
import { buildStory, captionFor, humanRelation, storyFrame } from "./story";
import type {
  EvidenceTier,
  ExplorerLevel,
  GraphMetadata,
  GraphView,
  RecordingStory,
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

const pageParameters = new URLSearchParams(window.location.search);
const automationMode = pageParameters.get("presentation") === "1";

export function App() {
  const [metadata, setMetadata] = useState<GraphMetadata | null>(null);
  const [view, setView] = useState<GraphView | null>(null);
  const [level, setLevel] = useState<"component" | "event">("event");
  const [relation, setRelation] = useState<Relation | "all">("all");
  const [minConfidence, setMinConfidence] = useState(
    numberParameter("min_confidence", 0.95),
  );
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
  const [breadcrumbs, setBreadcrumbs] = useState<string[]>(["Graph"]);
  const [story, setStory] = useState<RecordingStory | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [canvasReady, setCanvasReady] = useState(false);
  const overviewRequest = useRef<AbortController | null>(null);
  const automationStarted = useRef(false);

  const graphFingerprint = String(
    metadata?.viewer.graph_content_fingerprint ?? "unknown",
  );
  const requestedFilterKey = `${level}:${relation}:${minConfidence.toFixed(2)}:${evidenceTier}`;
  const [viewFilterKey, setViewFilterKey] = useState(requestedFilterKey);

  const loadOverview = useCallback(
    async (signal?: AbortSignal) => {
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
        );
        setView(next);
        setViewFilterKey(requestedFilterKey);
        setSelectedId(null);
        setDetail(null);
        setBreadcrumbs(["Graph", level === "component" ? "Components" : "Events"]);
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [level, relation, minConfidence, evidenceTier, requestedFilterKey],
  );

  useEffect(() => {
    void api
      .metadata()
      .then(setMetadata)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (metadata === null || story !== null || automationMode) return undefined;
    overviewRequest.current?.abort();
    const controller = new AbortController();
    overviewRequest.current = controller;
    const timer = window.setTimeout(() => void loadOverview(controller.signal), 150);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [metadata, loadOverview, story]);

  const enterStory = useCallback(
    async (confidence = minConfidence) => {
      overviewRequest.current?.abort();
      setLoading(true);
      setError(null);
      setCanvasReady(false);
      try {
        const highlights = integerParameter("highlights", 6);
        const plan = await api.recordingPlan(highlights, confidence);
        const frozen = await freezeLayout(
          plan.graph,
          plan.graph_fingerprint,
          `recording:${plan.min_confidence}:${plan.requested_limit}`,
        );
        const nextStory = buildStory(
          plan,
          frozen.view,
          frozen.layout.fingerprint,
          frozen.layout.metadata,
          {
            width: integerParameter("width", 1920),
            height: integerParameter("height", 1080),
            fps: integerParameter("fps", 30),
          },
          metadata?.package_version ?? "unknown",
          String(metadata?.viewer.client_fingerprint ?? "unknown"),
        );
        setStory(nextStory);
        setView(nextStory.graph);
        setFrame(0);
        setPlaying(false);
        setSelectedId(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoading(false);
      }
    },
    [metadata, minConfidence],
  );

  useEffect(() => {
    if (!automationMode || metadata === null || automationStarted.current) return;
    automationStarted.current = true;
    void enterStory();
  }, [metadata, enterStory]);

  useEffect(() => {
    if (!story || !playing) return undefined;
    let animation = 0;
    let previous = performance.now();
    let remainder = 0;
    const tick = (now: number) => {
      const elapsedFrames = ((now - previous) / 1_000) * story.viewport.fps + remainder;
      const wholeFrames = Math.floor(elapsedFrames);
      remainder = elapsedFrames - wholeFrames;
      previous = now;
      if (wholeFrames > 0) {
        setFrame((current) => {
          const next = Math.min(story.timeline.frame_count - 1, current + wholeFrames);
          if (next === story.timeline.frame_count - 1) setPlaying(false);
          return next;
        });
      }
      animation = requestAnimationFrame(tick);
    };
    animation = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animation);
  }, [story, playing]);

  useEffect(() => {
    if (!story) {
      delete window.__ODDSFOX_RECORDING__;
      return undefined;
    }
    const activeStory = story;
    window.__ODDSFOX_RECORDING__ = {
      get ready() {
        return canvasReady;
      },
      getStory: () => activeStory,
      getFrameCount: () => activeStory.timeline.frame_count,
      seek: async (requestedFrame: number) => {
        if (!Number.isInteger(requestedFrame) || requestedFrame < 0 || requestedFrame >= activeStory.timeline.frame_count) {
          throw new Error(`Frame must be an integer from 0 to ${activeStory.timeline.frame_count - 1}`);
        }
        flushSync(() => setFrame(requestedFrame));
        await document.fonts.ready;
        await nextAnimationFrame();
        await nextAnimationFrame();
      },
    };
    return () => {
      delete window.__ODDSFOX_RECORDING__;
    };
  }, [story, canvasReady]);

  const selectNode = useCallback(
    async (id: string) => {
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
    },
    [view, reasonFrom, reasonTo],
  );

  const selectEdge = useCallback(
    async (id: string) => {
      setSelectedId(id);
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
    },
    [view],
  );

  const openNode = useCallback(
    async (id: string) => {
      const selected = view?.nodes.find((node) => node.id === id);
      if (!selected || selected.level === "proposition") return;
      overviewRequest.current?.abort();
      setLoading(true);
      setError(null);
      try {
        if (selected.level === "event") {
          setView(await api.eventGraph(id, relation, minConfidence, evidenceTier));
          setViewFilterKey(`event:${id}:${requestedFilterKey}`);
          setBreadcrumbs(["Graph", "Events", selected.label]);
        } else {
          setView(await api.componentGraph(
            id,
            relation,
            minConfidence,
            evidenceTier,
          ));
          setViewFilterKey(`component:${id}:${requestedFilterKey}`);
          setBreadcrumbs(["Graph", selected.label, "Events"]);
        }
        setSelectedId(null);
        setDetail(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoading(false);
      }
    },
    [view, relation, minConfidence, evidenceTier, requestedFilterKey],
  );

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
    overviewRequest.current?.abort();
    setLoading(true);
    try {
      const next = await api.neighborhood(result.node_id);
      setView(next);
      setBreadcrumbs(["Graph", "Search", result.canonical_proposition]);
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

  if (story) {
    const state = storyFrame(story, frame);
    const highlight =
      state.shot.highlight_index === null
        ? null
        : story.highlights[state.shot.highlight_index];
    return (
      <main className="presentation-shell">
        <GraphCanvas
          view={story.graph}
          selectedId={null}
          graphFingerprint={story.graph_fingerprint}
          filterKey={`story:${story.layout_fingerprint}`}
          layoutNonce={0}
          story={story}
          frame={frame}
          onSelectNode={() => undefined}
          onSelectEdge={() => undefined}
          onReady={() => setCanvasReady(true)}
        />
        {state.overlay === "intro" && (
          <section className="story-title story-overlay">
            <p>OddsFox logic graph</p>
            <h1>Deterministic logical highlights</h1>
            <div>{story.mode} graph · {story.validation_status.replaceAll("_", " ")}</div>
            <small>{story.graph.nodes.length} propositions · {story.graph.edges.length} visible logical edges</small>
          </section>
        )}
        {state.overlay === "caption" && state.emphasis > 0 && highlight && (
          <section className="story-caption story-overlay">
            <p>Highlight {highlight.rank} of {story.highlights.length}</p>
            <h2>{captionFor(story, frame)}</h2>
            <div>
              <span>{humanRelation(highlight.relation)}</span>
              <span>{(highlight.confidence * 100).toFixed(1)}% confidence</span>
              <span>{highlight.evidence_tier.replaceAll("_", " ")}</span>
            </div>
            {highlight.explanation_excerpt && <small>{highlight.explanation_excerpt}</small>}
          </section>
        )}
        {state.overlay === "outro" && (
          <section className="story-title story-overlay">
            <p>Graph restored</p>
            <h1>{story.graph.nodes.length} propositions · {story.graph.edges.length} edges</h1>
            <div>{story.mode} · {story.validation_status.replaceAll("_", " ")}</div>
            <small>Graph {story.graph_fingerprint.slice(0, 12)}</small>
          </section>
        )}
        {!automationMode && (
          <nav className="story-controls" aria-label="Story preview controls">
            <button type="button" onClick={() => setPlaying((value) => !value)}>{playing ? "Pause" : "Play"}</button>
            <button type="button" className="secondary" onClick={() => seekHighlight(story, frame, -1, setFrame)}>Previous</button>
            <button type="button" className="secondary" onClick={() => seekHighlight(story, frame, 1, setFrame)}>Next</button>
            <input
              aria-label="Story position"
              type="range"
              min="0"
              max={story.timeline.frame_count - 1}
              value={frame}
              onChange={(event) => setFrame(Number(event.target.value))}
            />
            <span>{(frame / story.viewport.fps).toFixed(1)} / {story.timeline.duration_seconds.toFixed(1)}s</span>
            <button type="button" className="secondary" onClick={() => void enterStory(minConfidence)}>Regenerate</button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setStory(null);
                setCanvasReady(false);
                setPlaying(false);
              }}
            >Exit presentation</button>
          </nav>
        )}
        {loading && <div className="loading">Building story…</div>}
        {error && <div className="presentation-error" role="alert">{error}</div>}
      </main>
    );
  }

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

      <nav className="breadcrumbs" aria-label="Graph location">
        {breadcrumbs.map((crumb, index) => (
          <button key={`${crumb}-${index}`} type="button" disabled={index > 0} onClick={() => void loadOverview()}>{crumb}</button>
        ))}
      </nav>

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
            {isStatic && <option value="static">Exported {view?.level ?? "graph"}</option>}
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
          <button type="button" disabled={isStatic} onClick={() => void enterStory()}>Auto story</button>
        </div>
      </section>

      {error && <div className="error" role="alert">{error}</div>}
      {(view?.truncated_nodes || view?.truncated_edges) && <div className="notice">This view is bounded. Search or filter to inspect omitted nodes and edges.</div>}
      {typeof coverageGap === "number" && coverageGap > 0 && (
        <div className="notice">{(coverageGap * 100).toFixed(1)}% of eligible retrieved pairs remain unclassified; all selected propositions are still searchable.</div>
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
            <label>From proposition<input value={reasonFrom} onChange={(event) => setReasonFrom(event.target.value)} /></label>
            <label>To proposition<input value={reasonTo} onChange={(event) => setReasonTo(event.target.value)} /></label>
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

function numberParameter(name: string, fallback: number): number {
  const raw = pageParameters.get(name);
  if (raw === null || raw.trim() === "") return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function integerParameter(name: string, fallback: number): number {
  const value = numberParameter(name, fallback);
  return Number.isInteger(value) ? value : fallback;
}

function nextAnimationFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

function seekHighlight(
  story: RecordingStory,
  frame: number,
  direction: -1 | 1,
  setFrame: (frame: number) => void,
) {
  const starts = story.timeline.shots
    .filter((shot) => shot.kind === "highlight")
    .map((shot) => shot.start_frame);
  const current = Math.max(0, starts.findIndex((start, index) => frame >= start && frame < (starts[index + 1] ?? story.timeline.frame_count)));
  const next = Math.min(starts.length - 1, Math.max(0, current + direction));
  setFrame(starts[next]);
}
