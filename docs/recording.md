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
qualify: a three-second text-led intro, six seven-second relationship shots, and
a three-second summary. Intro and outro never show the complete graph. Each
shot reveals its two human claim cards and at most the stored bounded context.
There is no audio.

## Ranking

Eligible accepted non-`compatible` display-essential edges meet the requested
confidence and contain complete WC2026 team, stage, market, outcome, and
explanation context. Duplicate implications use directed endpoints; symmetric
relations use sorted endpoints. The deterministic base score is:

```text
0.25 × confidence
+ 0.25 × stage importance
+ 0.20 × structural reach
+ 0.15 × template novelty
+ 0.10 × evidence interest
+ 0.05 × relation interest
```

Stage importance is the highest endpoint progression level divided by five.
Structural reach logarithmically normalizes essential endpoint degree and
discounts dense components. Template novelty is the inverse square root of the
eligible stage/polarity template frequency. Evidence interest is 1.0 for
generative consensus, 0.80 for a deterministic rule, and 0.55 for a source
contract. Relation interest is 1.0 for implication, 0.90 for equivalence, 0.85
for mutual exclusion, and 0.60 for a complement.

Selection allows one team, stage/polarity template, and endpoint per story and
at most one highlight from a pathological component. It subtracts 0.08 for a
repeated relation, 0.04 for repeated evidence, 0.10 for a repeated target stage,
and 0.12 for a repeated component. Proposal ID resolves every tie. Every
feature, contribution, count, exclusion, penalty, base score, and selection
score is retained in the plan and story. If diversity leaves fewer than
requested, the recorder reports the shortfall and never relaxes confidence or
eligibility gates.

The context view always retains selected edges and endpoints, then takes at
most two qualifying incident edges per endpoint. The union is bounded at 96
nodes and 144 edges, while a normal shot exposes no more than six nodes and five
edges. Stable pruning removes context only and reports every count.

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

`story.json` and `recording_manifest.json` use v2 schemas. v1 plans and stories
are intentionally rejected. Static explorer exports remain investigation-only
because they do not provide the manifest-complete recording-plan service.
