import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
import { GraphCanvas } from "./GraphCanvas";
import { captionFor, humanRelation, storyFrame } from "./story";
import { evidenceLabel, validationLabel } from "./human";
import type { RecordingStory } from "./types";

interface Props {
  story: RecordingStory;
  automationMode: boolean;
  loading: boolean;
  error: string | null;
  onRegenerate: () => void;
  onExit: () => void;
}

export function Presentation({
  story,
  automationMode,
  loading,
  error,
  onRegenerate,
  onExit,
}: Props) {
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [canvasReady, setCanvasReady] = useState(false);
  const state = storyFrame(story, frame);
  const highlight = state.shot.highlight_index === null
    ? null
    : story.highlights[state.shot.highlight_index];

  useEffect(() => {
    if (!playing) return undefined;
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
    window.__ODDSFOX_RECORDING__ = {
      get ready() {
        return canvasReady;
      },
      getStory: () => story,
      getFrameCount: () => story.timeline.frame_count,
      seek: async (requestedFrame: number) => {
        if (!Number.isInteger(requestedFrame) || requestedFrame < 0 || requestedFrame >= story.timeline.frame_count) {
          throw new Error(`Frame must be an integer from 0 to ${story.timeline.frame_count - 1}`);
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

  return (
    <main className={`presentation-shell presentation-${state.overlay}`}>
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
        <section className="story-title story-overlay" aria-label="Story introduction">
          <p>OddsFox presents</p>
          <h1>FIFA World Cup 2026 Outcome Map</h1>
          <div>How team progression outcomes connect</div>
          <small>{story.highlights.length} verified relationships · {validationLabel(story.validation_status, story.mode)}</small>
        </section>
      )}
      {state.overlay === "caption" && state.emphasis > 0 && highlight && (
        <section className="story-caption story-overlay" aria-label={`Highlight ${highlight.rank}`}>
          <p>Connection {highlight.rank} of {story.highlights.length}</p>
          <h2>{captionFor(story, frame)}</h2>
          <div>
            <span>{humanRelation(highlight.relation)}</span>
            <span>{evidenceLabel(highlight.evidence_tier)}</span>
          </div>
          {highlight.explanation_excerpt && <small>{highlight.explanation_excerpt}</small>}
        </section>
      )}
      {state.overlay === "outro" && (
        <section className="story-title story-overlay" aria-label="Story summary">
          <p>World Cup outcome logic</p>
          <h1>{story.highlights.length} important connections, made clear</h1>
          <div>Team progression from the round of 32 to champion</div>
          <small>Built from validated Polymarket progression markets · Graph {story.graph_fingerprint.slice(0, 12)}</small>
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
          <button type="button" className="secondary" onClick={onRegenerate}>Regenerate</button>
          <button type="button" className="secondary" onClick={onExit}>Exit presentation</button>
        </nav>
      )}
      {loading && <div className="loading">Building story…</div>}
      {error && <div className="presentation-error" role="alert">{error}</div>}
    </main>
  );
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
  const current = Math.max(0, starts.findIndex(
    (start, index) => frame >= start && frame < (starts[index + 1] ?? story.timeline.frame_count),
  ));
  const next = Math.min(starts.length - 1, Math.max(0, current + direction));
  setFrame(starts[next]);
}
