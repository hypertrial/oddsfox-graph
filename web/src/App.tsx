import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { Explore } from "./Explore";
import { coverageLabel, validationLabel } from "./human";
import { parseRoute, type Route } from "./routes";
import type { GraphMetadata, RecordingStory } from "./types";

const Analyst = lazy(() => import("./Analyst").then((module) => ({ default: module.Analyst })));
const Presentation = lazy(() => import("./Presentation").then((module) => ({ default: module.Presentation })));

const pageParameters = new URLSearchParams(window.location.search);
const automationMode = pageParameters.get("presentation") === "1";

export function App() {
  const [metadata, setMetadata] = useState<GraphMetadata | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [story, setStory] = useState<RecordingStory | null>(null);
  const [storyLoading, setStoryLoading] = useState(false);
  const [storyError, setStoryError] = useState<string | null>(null);
  const automationStarted = useRef(false);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    void api.metadata().then(setMetadata).catch((reason: unknown) => setMetadataError(message(reason)));
  }, []);

  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => {
    if (!metadata || automationMode) return;
    document.title = route.kind === "analyst"
      ? "Analyst Graph · FIFA World Cup 2026 Outcome Map"
      : "FIFA World Cup 2026 Outcome Map";
    mainRef.current?.focus({ preventScroll: true });
  }, [route, metadata]);

  const enterStory = useCallback(async (confidence = 0.95) => {
    if (!metadata) return;
    setStoryLoading(true);
    setStoryError(null);
    try {
      const [{ freezeLayout }, { buildStory }] = await Promise.all([
        import("./layout"),
        import("./story"),
      ]);
      const highlights = integerParameter("highlights", 6);
      const plan = await api.recordingPlan(highlights, confidence);
      const frozen = await freezeLayout(
        plan.graph,
        plan.graph_fingerprint,
        `recording:${plan.min_confidence}:${plan.requested_limit}`,
      );
      setStory(buildStory(
        plan,
        frozen.view,
        frozen.layout.fingerprint,
        frozen.layout.metadata,
        {
          width: integerParameter("width", 1920),
          height: integerParameter("height", 1080),
          fps: integerParameter("fps", 30),
        },
        metadata.package_version,
        String(metadata.client_fingerprint ?? "unknown"),
      ));
    } catch (reason) {
      setStoryError(message(reason));
    } finally {
      setStoryLoading(false);
    }
  }, [metadata]);

  useEffect(() => {
    if (!automationMode || !metadata || automationStarted.current) return;
    automationStarted.current = true;
    void enterStory(numberParameter("min_confidence", 0.95));
  }, [metadata, enterStory]);

  if (story) {
    return (
      <Suspense fallback={<main className="startup-state" role="status">Opening presentation…</main>}>
        <Presentation
          story={story}
          automationMode={automationMode}
          loading={storyLoading}
          error={storyError}
          onRegenerate={() => void enterStory(numberParameter("min_confidence", 0.95))}
          onExit={() => {
            setStory(null);
            setStoryError(null);
          }}
        />
      </Suspense>
    );
  }

  if (metadataError) {
    return (
      <main className="startup-state" role="alert">
        <p className="eyebrow">OddsFox</p>
        <h1>The outcome map could not be opened</h1>
        <p>{metadataError}</p>
        <small>If this is an older graph or static export, regenerate it with oddsfox-graph 0.13.0.</small>
      </main>
    );
  }

  if (!metadata) {
    return <main className="startup-state" role="status"><span className="state-spinner" aria-hidden="true" />Loading the World Cup outcome map…</main>;
  }

  if (automationMode) {
    return (
      <main className="startup-state" role={storyError ? "alert" : "status"}>
        {storyError ?? "Preparing the recording story…"}
      </main>
    );
  }

  const isStatic = "static" in metadata.viewer && metadata.viewer.static === true;
  const buildMode = String(metadata.build.build_mode ?? metadata.viewer.build_mode ?? "unknown");
  const validationStatus = String(metadata.build.validation_status ?? metadata.viewer.validation_status ?? "unknown");
  const classificationStatus = String(metadata.coverage.classification_status ?? "");
  const classificationCoverage = metadata.coverage.classification_coverage;
  const graphOnly = route.kind === "analyst";

  return (
    <div className={`app-shell${graphOnly ? " graph-shell" : ""}`}>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      {!graphOnly && <header className="topbar">
        <a className="brand" href="#/explore" aria-label="FIFA World Cup 2026 Outcome Map home">
          <span className="brand-mark" aria-hidden="true">OF</span>
          <span>
            <span className="eyebrow">OddsFox</span>
            <strong>FIFA World Cup 2026 Outcome Map</strong>
            <small>Polymarket team progression markets</small>
          </span>
        </a>
        <div className="run-health" aria-label="Graph trust status">
          <span className={`mode-badge mode-${buildMode}`}>{buildMode === "fast" ? "Rule-based graph" : "Validated graph"}</span>
          <strong>{validationLabel(validationStatus, buildMode)}</strong>
          <span>{coverageLabel(classificationStatus, classificationCoverage)}</span>
          {isStatic && <span className="snapshot-badge">Static snapshot</span>}
        </div>
      </header>}
      {!graphOnly && <nav className="primary-nav" aria-label="Main navigation">
        <a href="#/explore" aria-current={route.kind !== "compare" ? "page" : undefined}>Explore</a>
        <a href="#/compare" aria-current={route.kind === "compare" ? "page" : undefined}>Compare outcomes</a>
        <a href="#/analyst">Graph</a>
      </nav>}
      {storyError && <div className="error" role="alert">{storyError}</div>}
      {storyLoading && <div className="global-progress" role="status">Building the presentation story…</div>}
      <main id="main-content" className="main-content" ref={mainRef} tabIndex={-1}>
        <Suspense fallback={<div className="page-state" role="status">Loading graph tools…</div>}>
          {route.kind === "analyst"
            ? <Analyst metadata={metadata} onEnterStory={(confidence) => void enterStory(confidence)} />
            : <Explore route={route} />}
        </Suspense>
      </main>
      {!graphOnly && <footer className="site-footer">
        <span>Outcome logic from the canonical OddsFox pipeline export.</span>
        <a href="#/analyst">Open graph</a>
      </footer>}
    </div>
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

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
