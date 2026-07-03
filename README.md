# oddsgraph

`oddsgraph` turns token-minute Polymarket odds into graph-ready parquet
artifacts. Each `clob_token_id` becomes a proposition node, then the batch build
emits market groups, logical edges, price-only edges, derived implications,
conditional probabilities, constraint rows, violations, optional coherence and
evaluation artifacts, and markdown reports.

This is a Python/DuckDB tool for offline analysis. It is not a live ingest or
trading system.

## Requirements

- Python 3.11 or newer.
- DuckDB from the Python package dependency in `pyproject.toml`.
- A parquet input with the schema described in
  [wc2026_token_minutely_odds_20260702T070755Z.md](wc2026_token_minutely_odds_20260702T070755Z.md).

The local WC2026 parquet is a large sample, about 621 MB. It is useful for
reproducing the project results, but generated outputs and large datasets should
stay outside source control.

## Get The Parquet

Use [hypertrial/oddsfox](https://github.com/hypertrial/oddsfox) to build and
export the source data. OddsFox documents the local pipeline in its
[quickstart](https://github.com/hypertrial/oddsfox/blob/main/docs/quickstart.md)
and exports `polymarket_marts.selected_token_minutely_odds` with
`scripts/export_selected_minutely_odds.py`.

The exported parquet should match the schema in
[wc2026_token_minutely_odds_20260702T070755Z.md](wc2026_token_minutely_odds_20260702T070755Z.md).

## Setup

From the repo root:

```bash
python -m pip install -e ".[dev]"
```

## Build Artifacts

Run a full build when you want the complete artifact set:

```bash
python -m oddsgraph.cli build \
  --input wc2026_token_minutely_odds_20260702T070755Z.parquet \
  --out output/wc2026
```

Run fast graph mode when you want graph/query artifacts quickly and can accept
lookback-scoped historical node and market statistics:

```bash
python -m oddsgraph.cli build \
  --input wc2026_token_minutely_odds_20260702T070755Z.parquet \
  --out output/wc2026-fast-graph \
  --fast-graph \
  --graph-lookback-days 30
```

Successful builds write `build_manifest.json` last. Treat that file as the
completion marker for a coherent output directory.

## Inspect Results

Search nodes:

```bash
python -m oddsgraph.cli search --out output/wc2026 --query "Brazil win World Cup"
```

Show high-volume nodes:

```bash
python -m oddsgraph.cli nodes --out output/wc2026 --top 50
```

Show trusted structural or semantic logic edges:

```bash
python -m oddsgraph.cli edges --out output/wc2026 --edge-type implies --top 50
```

Show price-threshold relationships that are not accepted as logic:

```bash
python -m oddsgraph.cli price-edges --out output/wc2026 --edge-type implies --top 50
```

Show pricing or logic violations:

```bash
python -m oddsgraph.cli violations --out output/wc2026 --top 50
```

Explain a node:

```bash
python -m oddsgraph.cli explain --out output/wc2026 --node "<token id or unique text>"
```

Ask for a conditional probability row:

```bash
python -m oddsgraph.cli condition \
  --out output/wc2026 \
  --a "Brazil reach the Round of 16" \
  --b "NOT(Will Brazil reach the Round of 16?)"
```

Summarize a completed build manifest:

```bash
python -m oddsgraph.cli benchmark-summary --out output/wc2026
```

## Documentation Map

- [docs/index.md](docs/index.md): handbook map and recommended reading order.
- [docs/cli.md](docs/cli.md): CLI commands, flags, query commands, and expected
  skipped-artifact errors.
- [docs/builds.md](docs/builds.md): build modes, optional inputs, manifest
  semantics, and artifact omission rules.
- [docs/artifacts.md](docs/artifacts.md): parquet artifact schemas and report
  reference.
- [docs/architecture.md](docs/architecture.md): build stages, major tables,
  edge lifecycle, coherence, evaluation, and performance hotspots.
- [docs/benchmarks.md](docs/benchmarks.md): benchmark methodology, dated local
  results, accepted/rejected optimizations, and summary command usage.

## Development Check

```bash
pytest -q
python -m oddsgraph.cli --help
```

The docs contract checks are part of `pytest -q`; there is no docs generator or
extra documentation dependency.

To run optional checks against an existing full WC2026 build:

```bash
pytest -q -m full_output
```
