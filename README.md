# oddsfox-graph

`oddsfox-graph` turns token-level Polymarket odds parquet into a structural
proposition graph. Each `clob_token_id` becomes a node. The batch build emits
market groups, accepted logic edges, and exact logic-only conditional edges.

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
- A parquet input from
  `polymarket_wc2026_marts.polymarket_wc2026_graph_token_hourly_odds` or a
  legacy hourly/minutely OddsFox export.

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
python -m pip install -e ".[dev]"
```

## Validation

```bash
python -m pytest -q
```

## Build Artifacts

```bash
python -m oddsfox_graph.cli build \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out "$ODDSFOX_DATA_DIR/artifacts/manual/wc2026"
```

Successful builds write `build_manifest.json` last. Treat that file as the
completion marker for a coherent output directory.

Published parquet artifacts:

- `nodes.parquet`
- `market_groups.parquet`
- `logic_edges.parquet`
- `conditional_edges.parquet`

Builds also write a slim `graph_snapshot.json` portable summary.

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
pytest -q
python -m oddsfox_graph.cli --help
```
