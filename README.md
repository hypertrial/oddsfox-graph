# oddsfox-graph

`oddsfox-graph` turns Polymarket market or token-odds parquet into a proposition
graph. Each `clob_token_id` becomes a node. The offline `build` command preserves
the WC2026 structural workflow; the v0.6.0 `discover` command uses self-hosted
open models for typed parsing, local retrieval/NLI, compact atomic relation
judgments, RC2 consistency solving, incremental execution, review tooling, and
complete provenance.

This is a Python/DuckDB tool for offline structural analysis. It does not score
prices, solve coherence LPs, or produce trading signals.

## Part Of OddsFox

`oddsfox-graph` consumes pipeline graph-export parquet and publishes offline
graph artifacts. The standalone
[`oddsfox-execution`](https://github.com/hypertrial/oddsfox-execution) service
accepts explicit order intents and does not consume graph output.

For the full repository and runtime flow, see the private parent
[OddsFox architecture](https://github.com/hypertrial/oddsfox/blob/main/docs/architecture.md).
The public warehouse remains documented in the
[pipeline system overview](https://github.com/hypertrial/oddsfox-pipeline/blob/main/docs/concepts/system-overview.md).

## Requirements

- Python 3.11 or newer.
- DuckDB from the Python package dependency in `pyproject.toml`.
- For `build`, a parquet input from
  `polymarket_wc2026_marts.polymarket_wc2026_graph_token_hourly_odds` or a
  legacy hourly/minutely OddsFox export.
- For `discover`, either a compact market snapshot with `market_id`, `question`,
  `outcomes`, and `clob_token_ids`, or an existing OddsFox minutely/hourly
  export.

## Get The Parquet

Use [hypertrial/oddsfox-pipeline](https://github.com/hypertrial/oddsfox-pipeline) to build and
export the source data. For WC2026 graph builds, export
`polymarket_wc2026_marts.polymarket_wc2026_graph_token_hourly_odds`:

```bash
export ODDSFOX_DATA_DIR="${ODDSFOX_DATA_DIR:-/Volumes/Mac SSD/hypertrial_trilemma/hypertrial/OddsFox/.runtime}"
mkdir -p "$ODDSFOX_DATA_DIR/exports"
uv run python scripts/export_polymarket_wc2026_graph_hourly_odds.py \
  --snapshot-copy \
  --output "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet"
```

This graph export carries both Yes/No tokens and dbt-clean team, stage,
progression-token, and opposite-token semantics. Legacy OddsFox hourly/minutely
exports remain supported; regex/taxonomy parsing is used only when semantic
columns are absent.

## Setup

From the repo root:

```bash
python -m pip install -c constraints-dev.txt -e ".[dev]"
```

Install the optional discovery runtime for live discovery:

```bash
python -m pip install -c constraints-dev.txt -e ".[discovery]"
```

Legacy `build` remains DuckDB-only. Discovery has no API-key or proprietary
inference path. It never downloads or launches model weights implicitly.
Provision the pinned MiniLM embedding and ModernBERT NLI revisions in the local
Hugging Face cache before discovery; both loaders run with local-only access.

For M4 development, launch the default Apache-2.0 Qwen3-4B Q8 GGUF:

```bash
llama-server \
  --model /models/Qwen3-4B-Q8_0.gguf \
  --alias Qwen/Qwen3-4B-GGUF:Q8_0 \
  --host 127.0.0.1 --port 8080 --ctx-size 8192
```

Linux GPU deployments use vLLM with the same Chat Completions schema. Both
runtimes are external and must be declared in a content-bound model manifest.

## Validation

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m ruff check .
python -m mypy
python -m build
python -m oddsfox_graph.cli --help
```

## Build Artifacts

```bash
python -m oddsfox_graph.cli build \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026"
```

Successful builds write `build_manifest.json` last. Treat that file as the
completion marker for a coherent output directory.

Legacy builds publish:

- `nodes.parquet`
- `market_groups.parquet`
- `logic_edges.parquet`
- `conditional_edges.parquet`

Builds also write a portable `graph_snapshot.json` summary.

## Automated Discovery

The supplied local catalog is the canonical smoke input:

```bash
python -m oddsfox_graph.cli model-manifest \
  --model-path /models/Qwen3-4B-Q8_0.gguf \
  --model-id Qwen/Qwen3-4B-GGUF:Q8_0 \
  --revision <upstream-revision> \
  --license Apache-2.0 \
  --runtime llama.cpp \
  --llm-base-url http://127.0.0.1:8080/v1 \
  --output model-manifest.json

python -m oddsfox_graph.cli model-check \
  --model-manifest model-manifest.json \
  --llm-base-url http://127.0.0.1:8080/v1

python -m oddsfox_graph.cli discover \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-smoke \
  --cache-dir .cache/oddsfox-graph \
  --model-manifest model-manifest.json
```

`data/` is intentionally unversioned; place the supplied catalog at that path
before running the smoke commands.

The catalog is deterministically capped by highest `volume`, then `market_id`,
to honor `--max-propositions` (default 5,000). Discovery first selects from
lightweight market summaries and only materializes full arrays for retained
markets. Unusable source markets and selection counts are recorded in the
manifest. The default candidate ceiling is 400,000 while classification remains
bounded at 5,000 pairs. Reproduce the completed run without a server request:

```bash
python -m oddsfox_graph.cli discover \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-smoke \
  --cache-dir .cache/oddsfox-graph \
  --model-manifest model-manifest.json \
  --offline
```

Discovery additionally publishes `propositions.parquet`,
`relation_candidates.parquet`, `rejected_edges.parquet`,
`parse_errors.parquet`, and `review_queue.parquet`, with reusable implementation
state under `state/`. v0.6 state includes structured block/reason contributions
and `execution_plan.parquet`, which evaluation verifies instead of trusting
manifest reuse counters. Export and score a legacy v0.3 human review:

```bash
python -m oddsfox_graph.cli review-export \
  --out output/discovery-smoke \
  --output output/discovery-smoke/review.csv

python -m oddsfox_graph.cli review-score \
  --out output/discovery-smoke \
  --labels output/discovery-smoke/review.csv
```

Cache entries explicitly distinguish successful output, stable failure, and an
exhausted transient failure. Offline mode reproduces every recorded terminal
outcome; a later online run retries transient failures instead of treating them
as permanent. The manifest separates current-request token usage from
`cached_origin` and `accounted_total` usage.

For v0.6, export two blinded reviewer files, compile only completed independent
reviews and adjudicated disagreements, then evaluate:

```bash
python -m oddsfox_graph.cli benchmark-export \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-smoke \
  --output-dir output/benchmark-review

python -m oddsfox_graph.cli benchmark-compile \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --review-a output/benchmark-review/reviewer-a.csv \
  --review-b output/benchmark-review/reviewer-b.csv \
  --adjudication output/benchmark-review/adjudication.csv \
  --sampling-manifest output/benchmark-review/sampling_manifest.json \
  --output oddsfox_graph/benchmarks/v0.6.0.parquet

python -m oddsfox_graph.cli model-profile \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --benchmark oddsfox_graph/benchmarks/v0.6.0.parquet \
  --cache-dir .cache/oddsfox-graph \
  --model-manifest model-manifest.json \
  --out output/model-profile

python -m oddsfox_graph.cli evaluate \
  --out output/discovery-smoke \
  --benchmark oddsfox_graph/benchmarks/v0.6.0.parquet \
  --compute-profile compute-profile.json
```

Benchmark labels and notes must be genuine; the tool never fabricates them.
The supplied catalog must have SHA-256
`790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2`.
Rule publication is benchmark-gated, and `--require-ready` fails unless every
release criterion passes. Use `--incremental-from` with a distinct,
manifest-complete output to reuse unchanged work; results must be logically
identical to a clean build.

Without a compiled benchmark, only same-market complement and categorical
exclusion facts publish deterministically. Other rules remain experimental.
`--allow-unbenchmarked-rules` restores the diagnostic opt-in behavior for
diagnostics, records the override, and makes the run ineligible for
`READY_TO_SCALE`.

The protected manual release workflow consumes the real parquet, complete
cache, immutable 5,000/20,000 baselines, expected online hashes, benchmark v2,
model manifest/profile, calibration report, and compute profile. The equivalent
local gate is:

```bash
python scripts/create_release_fixture_manifest.py \
  --fixture-root <release-fixture>

python scripts/validate_discovery_release.py \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --cache-dir <complete-cache> \
  --baseline-dir <completed-online-baselines> \
  --work-dir output/release-validation \
  --expected-hashes <expected-artifact-hashes.json> \
  --benchmark <compiled-benchmark.parquet> \
  --compute-profile <compute-profile.json> \
  --model-manifest <model-manifest.json> \
  --model-profile <model-profile.json> \
  --calibration-report <calibration-report.json> \
  --fixture-manifest <fixture-manifest.json>
```

Use `scripts/benchmark_discovery.py` for process-isolated clean, offline, and
one-market incremental measurements at 500, 2,000, 5,000, and 20,000
propositions. It uses the supplied parquet plus deterministic fake model
adapters and verifies offline and incremental logical hashes.

## Inspect Results

Search nodes:

```bash
python -m oddsfox_graph.cli search --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026" --query "Brazil"
```

Show nodes:

```bash
python -m oddsfox_graph.cli nodes --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026" --top 50
```

Show logic edges:

```bash
python -m oddsfox_graph.cli edges --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026" --edge-type implies --top 50
```

Ask for a conditional edge:

```bash
python -m oddsfox_graph.cli condition \
  --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026" \
  --a 60941235333934119537308581623022145063589498358463811604437431757990716193139 \
  --b 69254358704504551873876012384649223770132435379419074198292590735170180021451
```

Summarize a completed build manifest:

```bash
python -m oddsfox_graph.cli benchmark-summary --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026"
```

## Documentation Map

- [docs/index.md](docs/index.md): handbook map and recommended reading order.
- [docs/cli.md](docs/cli.md): CLI commands and flags.
- [docs/builds.md](docs/builds.md): build flow and manifest semantics.
- [docs/artifacts.md](docs/artifacts.md): parquet artifact schemas and reports.
- [docs/architecture.md](docs/architecture.md): build stages and edge lifecycle.
- [docs/benchmarks.md](docs/benchmarks.md): timing summary usage.

## Development Check

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m ruff check .
python -m mypy
python -m build
python -m oddsfox_graph.cli --help
```
