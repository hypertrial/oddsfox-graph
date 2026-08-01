# Deterministic graph recording

## Setup

Recording is an optional local runtime. Base installs, graph queries, discovery,
and CLI help do not import Playwright.

```bash
python -m pip install -e '.[recording]'
python -m playwright install chromium
ffmpeg -version
```

Missing Python Playwright, Chromium, or FFmpeg produces the corresponding
installation command before a browser or output directory is started.

## Command

```bash
oddsfox-graph record \
  --out output/fast \
  --destination recordings/fast-logic \
  --highlights 6 \
  --min-confidence 0.95 \
  --width 1920 --height 1080 --fps 30 \
  --progress-format plain
```

The destination must be new and disjoint from the manifest-complete fast or full
graph directory. The command never modifies discovery artifacts. It ranks,
constructs the story, renders, encodes, validates, hashes, and publishes without
manual editing. If fewer edges qualify, it records the available set without
lowering the threshold. If none qualify, it fails before recording.

The defaults produce 48 seconds and exactly 1,440 frames when six highlights
qualify: a three-second intro, six seven-second edge shots, and a three-second
outro. Each shot uses two seconds of cubic camera movement, one second of
endpoint reveal, and four seconds of captioned edge emphasis. There is no audio.

## Ranking

Eligible accepted non-`compatible` edges meet the requested confidence. Duplicate
implications use directed endpoints; symmetric relations use sorted endpoints.
The deterministic base score is:

```text
0.30 × confidence
+ 0.25 × scope
+ 0.20 × structural reach
+ 0.15 × evidence interest
+ 0.10 × relation interest
```

Scope is 1.0 across events, 0.65 across markets in one event, and 0.25 within a
market. Evidence interest is 1.0 for generative consensus, 0.80 for a
deterministic rule, and 0.55 for a source contract. Relation interest is 1.0 for
implication, 0.90 for equivalence, 0.85 for mutual exclusion, and 0.60 for a
complement. Structural reach logarithmically normalizes the endpoint degree sum.

Greedy selection subtracts 0.08 for each selected edge with the same relation,
0.10 for the same evidence tier, 0.15 for the same unordered event pair, and
0.20 per shared endpoint. Proposal ID resolves every tie. Every feature,
contribution, count, penalty, base score, and selection score is retained in the
plan and story.

The context view always retains selected edges and endpoints, then considers the
25 strongest qualifying incident edges per endpoint. It is bounded at 750 nodes
and 1,500 edges; stable pruning removes context only and reports all counts.

## Bundle and encoding

```text
BUNDLE_DIR/
  recording.mp4
  story.json
  recording_manifest.json
```

Frames are addressed synchronously in the browser and streamed as PNG bytes to
FFmpeg stdin. Encoding uses H.264 (`libx264`), CRF 18, the medium preset,
`yuv420p`, no audio, fast-start placement, and stripped metadata. The recorder
hashes the ordered PNG stream while encoding and validates the completed MP4
with FFmpeg. The manifest completion marker is written last in a sibling staging
directory, which is renamed into place only on success.

## Determinism and troubleshooting

Selection, score breakdowns, story JSON, and frozen coordinates are bound to the
graph and parameters. Visual frames are reproducible only with the same graph,
parameters, packaged client, Chromium, FFmpeg, platform, and fonts. Browser,
codec, driver, rasterization, or font differences can change frame or MP4 hashes
without changing selected logical edges.

- “Recording support is not installed”: install `oddsfox-graph[recording]`.
- “Playwright Chromium is missing”: run `python -m playwright install chromium`.
- “FFmpeg is required”: install FFmpeg and put `ffmpeg` on `PATH`.
- “No accepted … edges”: lower `--min-confidence` deliberately or inspect the
  graph; the recorder never lowers it automatically.
- An interrupted or failed run leaves the requested destination absent and
  removes its staging directory. An already existing destination is untouched.

Static explorer exports are investigation-only in v1 because they do not carry
the original completed graph manifest and recording-plan service.
