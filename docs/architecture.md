# Architecture

`oddsfox-graph` is a sequential DuckDB build. Python orchestrates taxonomy rules;
DuckDB holds intermediate tables until parquet export.

The source parquet can be minutely or hourly. Both formats normalize into
`input_prices`. Price columns remain in the input schema for pipeline
compatibility and are not used for edge acceptance. Out-of-range prices are not
range-gated by the structural builder.

## Build Pipeline

1. Detect source granularity and normalize input into `input_prices`.
2. Validate schema and row invariants.
3. Deduplicate token price buckets into `token_minute_prices`.
4. Build market token counts and token identity stats.
5. Build `nodes_v` with taxonomy semantics.
6. Generate structural candidate edges.
7. Accept structural candidates as `logic_edges_v`.
8. Write exact logic-only conditionals.
9. Export parquet artifacts, markdown reports, snapshot, and manifest.

## Major Tables And Views

- `input_prices`: normalized source rows.
- `token_minute_prices`: one latest row per `(clob_token_id, odds_minute_epoch)`.
- `market_token_counts`: expected token cardinality per market.
- `token_stats`: one identity row per token.
- `nodes_v`: proposition nodes with taxonomy fields.
- `candidate_edges_v`: structural candidates before acceptance.
- `logic_edges_v`: accepted logic edges.
- `conditional_edges_v`: exact 0/1 conditionals.

## Edge Lifecycle

Candidate edges come from four sources:

- `same_market`
- `exact_duplicate_same_event`
- `semantic_single_winner`
- `semantic_stage_progression`

Accepted logic edges use bases:

- `same_market`
- `exact_duplicate`
- `single_winner_family`
- `stage_progression_rule`

Stage-progression candidates already emit the full implication hull across
ranks (`stage_rank` greater-than joins), so there is no separate transitive
closure stage. Price-threshold candidates are not generated.

## Conditionals

Conditionals are derived only from accepted logic edges:

- complement / mutually exclusive → exact 0 both directions
- equivalent → exact 1 both directions
- implies → exact `P(dst|src) = 1`

No Frechet bounds and no price-ratio implication reverse rows are written.
