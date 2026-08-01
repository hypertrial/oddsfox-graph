# OddsFox Graph

OddsFox Graph 0.12 builds and visualizes the logical connections in the
pipeline's FIFA World Cup 2026 team-progression market export. The human
explorer is deliberately scoped to that trusted universe; generic football
props such as exact score, corners, and player markets are not admitted.
Discovery has two explicit modes:

- `fast` is model-free, complete in node coverage, and publishes only exact
  source-contract or strict deterministic-rule edges. It is the release-gated
  path and targets a ready explorer in under two minutes on an Apple M4 Air.
- `full` upgrades that deterministic core with local embeddings, USearch ANN,
  NLI vetoes, and Qwen/Granite consensus. It is functional but carries the
  `EXPERIMENTAL_FULL` validation status until separate live certification.

Fast mode is deliberately conservative: coverage means every valid market in
the supplied pipeline export, not every WC2026 market on Polymarket. Markets
without hourly rows are absent upstream. Full mode evaluates a bounded candidate
set and never treats unassessed pairs as unrelated.

## Install

```bash
python -m pip install -e '.[dev]'
```

Install deterministic MP4 recording separately so ordinary graph queries and
CLI help do not import a browser runtime:

```bash
python -m pip install -e '.[recording]'
python -m playwright install chromium
```

llama.cpp or vLLM and all model weights remain external. The local wrapper keeps
models, caches, outputs, logs, and temporary files below `.oddsfox-runtime/` on
the SSD containing this checkout.

## Input

The explorer input is
`polymarket-wc2026-graph-hourly-v1`, exported from the sibling
`oddsfox-pipeline` repository's public
`polymarket_wc2026_graph_token_hourly_odds` mart. It contains both Yes and No
tokens plus authoritative team, stage, direction, and progression semantics.
When the export carries the optional source market `end_date`, the plotter uses
it only for left-to-right placement and never as evidence for a logical edge.
The current v1 pipeline contract without that column remains valid and uses a
deterministic team-by-progression grid instead; OddsFox Graph never substitutes
odds timestamps for a missing market close time.
Do not recreate membership by filtering questions, tags, or slugs.

```bash
cd ../oddsfox-scraper/components/oddsfox-pipeline
uv run python scripts/run_scope.py polymarket:wc2026 --step full
uv run python scripts/export_polymarket_wc2026_graph_hourly_odds.py \
  --snapshot-copy --output "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet"
```

The older `polymarket-market-snapshot-v1` contract remains available to the
graph library, but generic outputs are not a supported human explorer surface
in 0.12.

## Fast graph

```bash
oddsfox-graph doctor --mode fast \
  --input-profile polymarket-wc2026-graph-hourly-v1 \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" --out output/fast
oddsfox-graph discover --mode fast \
  --input-profile polymarket-wc2026-graph-hourly-v1 \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out output/fast --deadline-seconds 120 --progress-format plain
oddsfox-graph serve --out output/fast --open-browser
oddsfox-graph explorer-export --out output/fast \
  --destination output/wc2026-html --scope graph
oddsfox-graph record --out output/fast --destination recordings/fast-story
```

WC2026 input always selects the complete structurally valid export and rejects
`--max-propositions`, which could break a team's progression chain. Fast mode
neither starts nor imports inference, embedding, NLI, or cache code.

## Full enrichment

Create and qualify both local model manifests out of band, then run:

```bash
oddsfox-graph qualify \
  --input-profile polymarket-wc2026-graph-hourly-v1 \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out qualification --cache-dir cache \
  --primary-model-manifest primary.json --verifier-model-manifest verifier.json \
  --compute-profile compute.json
oddsfox-graph discover --mode full \
  --input-profile polymarket-wc2026-graph-hourly-v1 \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out output/full --cache-dir cache \
  --automation-profile qualification/automation_profile.json \
  --primary-model-manifest primary.json --verifier-model-manifest verifier.json \
  --compute-profile compute.json --deadline-seconds 3600
```

The WC2026 qualification profile parses every collapsed progression market and
uses a deterministic, market-disjoint 60/40 split for 5,000 controlled pair
cases. The profile must exactly bind the v0.12 extractor, ANN, NLI, prompts,
schemas, settings, manifests, runtime pair, and WC input contract. A deadline cutoff stops new model work,
finishes in-flight requests, and publishes the valid deterministic core plus any
accepted enrichment; the command exits nonzero when the requested SLA is missed.

The explorer filters `source_contract`, `deterministic_rule`, and
`generative_consensus` evidence. Python callers use
`oddsfox_graph.graph.Graph` for typed queries, proofs, and why-not diagnostics.

See [architecture](docs/architecture.md), [discovery](docs/discovery.md),
[explorer](docs/explorer.md), [qualification](docs/qualification.md),
[artifacts](docs/artifacts.md), [CLI](docs/cli.md),
[recording](docs/recording.md), [performance](docs/performance.md), and
[release validation](docs/release.md).
