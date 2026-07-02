# Build Modes

The default build remains the compatibility contract. It writes the full current
artifact set and keeps historical statistics full-history unless a mode says
otherwise.

## Full Build

```bash
python -m oddsgraph.cli build \
  --input wc2026_token_minutely_odds_20260702T070755Z.parquet \
  --out output/wc2026
```

Full builds write all default parquet artifacts, all default reports, and
`build_manifest.json`. They materialize the full deduplicated minute history for
`prices.parquet` and historical node and market statistics.

## Optional Inputs

- `--quotes quotes.parquet`: optional bid/ask history with `clob_token_id`,
  `odds_timestamp_epoch`, `bid`, and `ask`. When provided, midpoint prices and
  half-spread noise floors are used for scoring.
- `--resolutions resolutions.parquet`: optional resolved outcomes with either
  `clob_token_id` or (`market_id`, `outcome_label`), plus `payout` and
  `resolved_at`. When provided, the build writes `evaluation.parquet` and
  `reports/evaluation.md`.
- `--taxonomy path.json`: event taxonomy for stage progression and
  single-winner families. Defaults to `oddsgraph/taxonomies/wc2026.json`.

## Optional Output Modes

### `--skip-prices`

Omits `prices.parquet`. Graph artifacts, reports, and query commands that read
nodes, edges, violations, or conditionals still work. Commands that require
`prices.parquet` report that it was intentionally not generated.

### `--skip-coherence`

Omits `coherence.parquet` and `coherence_repairs.parquet`. Conditional rows
fall back to current pair prices, and violations omit `global_incoherence`
rows. The `coherence` command reports that the artifact was intentionally not
generated and names `--skip-coherence`.

### `--fast-graph`

Opt-in graph inspection mode. It implies `write_prices=False` and
`solve_coherence=False`, so it omits `prices.parquet`, `coherence.parquet`, and
`coherence_repairs.parquet`. It preserves graph/query artifacts:
`nodes.parquet`, `market_groups.parquet`, `candidate_edges.parquet`,
`logic_edges.parquet`, `price_edges.parquet`, `derived_edges.parquet`,
`constraint_hyperedges.parquet`, `conditional_edges.parquet`,
`violations.parquet`, `calibration.parquet`, reports, and the manifest.

`--graph-lookback-days N` controls the fast graph history window. It defaults to
`30`, must be positive, and is only valid with `--fast-graph`.

Fast graph mode still computes current prices from each market's latest complete
minute. Historical node fields such as `active_minutes`, `mean_price`,
`mean_price_devig`, `min_price`, `max_price`, and market fields such as
`mean_sum_price` are lookback-scoped. The manifest marks this with
`stats.history_mode = "fast_graph_lookback"`.

## Manifest Semantics

`build_manifest.json` is written last and is the completion marker for a coherent
output directory. It lists only artifacts and reports intentionally written by
that build.

Top-level manifest fields:

- `input`: input parquet path.
- `quotes`: optional quotes path, or `null`.
- `resolutions`: optional resolutions path, or `null`.
- `taxonomy`: taxonomy metadata.
- `effective_thresholds`: calibrated thresholds used for graph acceptance.
- `lp_warnings`: warnings emitted by event-level LP coherence.
- `build_options`: explicit options that affected artifact generation.
- `artifacts`: parquet artifact filenames written for this build.
- `reports`: markdown report paths written for this build.
- `stats`: summary counts and runtime metrics.
- `stage_timings`: elapsed seconds by build stage.

`taxonomy` contains:

- `name`
- `path`
- `hash`

`build_options` contains:

- `write_prices`
- `solve_coherence`
- `fast_graph`
- `graph_lookback_days`

`stats` includes graph counts and `history_mode`, which is either `full` or
`fast_graph_lookback`.

Query commands read the manifest before opening artifacts. That lets them
distinguish intentionally skipped artifacts from missing or stale output files.
