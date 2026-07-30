# Builds

v0.2.0 has one build flow: structural graph construction.

## Command

```bash
python -m oddsfox_graph.cli build --input <parquet> --out <dir> [--taxonomy <json>]
```

## Stages

1. Detect input granularity and normalize into `input_prices`.
2. Deduplicate token buckets and build identity tables.
3. Build `nodes_v` and write `nodes.parquet` / `market_groups.parquet`.
4. Generate structural candidates and accept them as logic edges.
5. Write exact logic-only `conditional_edges.parquet`.
6. Write `graph_snapshot.json`, reports, and `build_manifest.json`.

## Manifest

Successful builds write `build_manifest.json` last. Fields:

| Field | Meaning |
|---|---|
| `input` | Source parquet path |
| `input_format` | `minutely` or `hourly` |
| `input_granularity_seconds` | Source bucket size in seconds |
| `taxonomy` | Object with `name`, `path`, and `hash` |
| `artifacts` | Published parquet/json artifact names |
| `reports` | Markdown report paths under `reports/` |
| `stats` | Row counts and runtime |
| `stage_timings` | Per-stage seconds |

## Scratch Database

Builds also write `oddsfox_graph.duckdb` under `--out` as a working database.
It is cleared on rebuild and is not a published contract artifact.

## Omitted Surfaces

The following are intentionally not produced in v0.2.0:

- prices / price-only edges
- calibration / coherence / violations / evaluation
- knockout artifacts
- candidate-edge parquet exports

Rebuilds into an existing output directory also delete those legacy artifact and
report names so stale v0.1 files cannot remain beside the structural set.
