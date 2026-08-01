import type {
  CameraState,
  LayoutMetadata,
  RecordingPlan,
  RecordingStory,
  StoryFrameState,
  StoryShot,
} from "./types";

const FULL_CAMERA: CameraState = { x: 0.5, y: 0.5, ratio: 1, angle: 0 };

export function buildStory(
  plan: RecordingPlan,
  graph: RecordingPlan["graph"],
  layoutFingerprint: string,
  layoutMetadata: LayoutMetadata,
  viewport: { width: number; height: number; fps: number },
  clientVersion: string,
  clientFingerprint: string,
): RecordingStory {
  const { fps } = viewport;
  const introFrames = 3 * fps;
  const highlightFrames = 7 * fps;
  const outroFrames = 3 * fps;
  const shots: StoryShot[] = [];
  shots.push({
    kind: "intro",
    highlight_index: null,
    start_frame: 0,
    end_frame: introFrames,
    zoom_end_frame: introFrames,
    reveal_end_frame: introFrames,
    camera_from: FULL_CAMERA,
    camera_to: FULL_CAMERA,
  });
  let previousCamera = FULL_CAMERA;
  plan.highlights.forEach((highlight, index) => {
    const start = introFrames + index * highlightFrames;
    const camera = cameraForHighlight(graph, highlight.source_id, highlight.target_id);
    shots.push({
      kind: "highlight",
      highlight_index: index,
      start_frame: start,
      end_frame: start + highlightFrames,
      zoom_end_frame: start + 2 * fps,
      reveal_end_frame: start + 3 * fps,
      camera_from: previousCamera,
      camera_to: camera,
    });
    previousCamera = camera;
  });
  const outroStart = introFrames + plan.highlights.length * highlightFrames;
  shots.push({
    kind: "outro",
    highlight_index: null,
    start_frame: outroStart,
    end_frame: outroStart + outroFrames,
    zoom_end_frame: outroStart + outroFrames,
    reveal_end_frame: outroStart + outroFrames,
    camera_from: previousCamera,
    camera_to: FULL_CAMERA,
  });
  const frameCount = outroStart + outroFrames;
  return {
    schema_version: "oddsfox-recording-story-v1",
    graph_fingerprint: plan.graph_fingerprint,
    source_fingerprint: plan.graph_fingerprint,
    client_version: clientVersion,
    client_fingerprint: clientFingerprint,
    layout_version: "hierarchical-fa2-v1",
    layout_fingerprint: layoutFingerprint,
    ranking_version: plan.ranking_version,
    mode: plan.mode,
    validation_status: plan.validation_status,
    graph,
    highlights: plan.highlights,
    context_pruning: plan.context_pruning,
    viewport,
    timeline: {
      frame_count: frameCount,
      duration_seconds: frameCount / fps,
      intro_seconds: 3,
      highlight_seconds: 7,
      outro_seconds: 3,
      shots,
    },
    presentation_theme: {
      background: "#070b12",
      foreground: "#f4f7fb",
      muted: "#9eabc0",
      accent: "#77a8ff",
    },
    layout_metadata: layoutMetadata,
  };
}

export function storyFrame(story: RecordingStory, requestedFrame: number): StoryFrameState {
  const frame = Math.max(0, Math.min(story.timeline.frame_count - 1, Math.floor(requestedFrame)));
  const shot =
    story.timeline.shots.find((candidate) => frame < candidate.end_frame) ??
    story.timeline.shots.at(-1)!;
  const highlight =
    shot.highlight_index === null ? null : story.highlights[shot.highlight_index];
  let progress = normalized(frame, shot.start_frame, shot.zoom_end_frame);
  if (shot.kind === "outro") {
    progress = normalized(frame, shot.start_frame, shot.end_frame - 1);
  }
  const camera = interpolateCamera(shot.camera_from, shot.camera_to, cubicEase(progress));
  let reveal = 0;
  let emphasis = 0;
  if (highlight && frame >= shot.zoom_end_frame) {
    reveal = normalized(frame, shot.zoom_end_frame, shot.reveal_end_frame);
    if (frame >= shot.reveal_end_frame) {
      const holdFrame = frame - shot.reveal_end_frame;
      const pulse = Math.sin((holdFrame / story.viewport.fps) * Math.PI * 1.25);
      emphasis = 0.92 + 0.08 * pulse * pulse;
    } else {
      emphasis = reveal * 0.8;
    }
  }
  return {
    frame,
    shot,
    camera,
    highlightedEdge:
      highlight && frame >= shot.reveal_end_frame ? highlight.proposal_id : null,
    highlightedNodes: new Set(
      highlight && frame >= shot.zoom_end_frame
        ? [highlight.source_id, highlight.target_id]
        : [],
    ),
    reveal,
    emphasis,
    overlay: shot.kind === "highlight" ? "caption" : shot.kind,
  };
}

export function captionFor(story: RecordingStory, frame: number): string | null {
  const state = storyFrame(story, frame);
  if (state.shot.highlight_index === null) return null;
  const highlight = story.highlights[state.shot.highlight_index];
  return `${highlight.source_label} ${humanRelation(highlight.relation)} ${highlight.target_label}`;
}

export function humanRelation(relation: string): string {
  return relation.replaceAll("_", " ");
}

function cameraForHighlight(
  graph: RecordingPlan["graph"],
  sourceId: string,
  targetId: string,
): CameraState {
  const included = new Set([sourceId, targetId]);
  const endpoints = new Set(included);
  for (const edge of graph.edges) {
    if (endpoints.has(edge.source) || endpoints.has(edge.target)) {
      included.add(edge.source);
      included.add(edge.target);
    }
  }
  const all = graph.nodes;
  const focused = all.filter((node) => included.has(node.id));
  if (all.length === 0 || focused.length === 0) return FULL_CAMERA;
  const global = bounds(all);
  const local = bounds(focused);
  const globalWidth = Math.max(1, global.maxX - global.minX);
  const globalHeight = Math.max(1, global.maxY - global.minY);
  const localWidth = Math.max(1, local.maxX - local.minX);
  const localHeight = Math.max(1, local.maxY - local.minY);
  const globalSpan = Math.max(globalWidth, globalHeight);
  const localSpan = Math.max(localWidth, localHeight);
  return {
    x: clamp(((local.minX + local.maxX) / 2 - global.minX) / globalWidth, 0, 1),
    y: clamp(((local.minY + local.maxY) / 2 - global.minY) / globalHeight, 0, 1),
    ratio: clamp((localSpan / globalSpan) * 1.36, 0.08, 1),
    angle: 0,
  };
}

function bounds(nodes: RecordingPlan["graph"]["nodes"]) {
  return {
    minX: Math.min(...nodes.map((node) => node.x - node.size)),
    maxX: Math.max(...nodes.map((node) => node.x + node.size)),
    minY: Math.min(...nodes.map((node) => node.y - node.size)),
    maxY: Math.max(...nodes.map((node) => node.y + node.size)),
  };
}

function interpolateCamera(from: CameraState, to: CameraState, amount: number): CameraState {
  return {
    x: lerp(from.x, to.x, amount),
    y: lerp(from.y, to.y, amount),
    ratio: lerp(from.ratio, to.ratio, amount),
    angle: 0,
  };
}

function normalized(value: number, start: number, end: number): number {
  if (end <= start) return 1;
  return clamp((value - start) / (end - start), 0, 1);
}

function cubicEase(value: number): number {
  return value < 0.5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2;
}

function lerp(from: number, to: number, amount: number): number {
  return from + (to - from) * amount;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
