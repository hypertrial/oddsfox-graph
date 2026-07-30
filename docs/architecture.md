# Architecture

`oddsfox-graph` has two publication paths. The legacy `build` path is a
sequential DuckDB build using the WC2026 taxonomy. The `discover` path uses
Python for typed parsing, retrieval, rules, classification, and consistency,
then DuckDB for deterministic artifact publication.

The legacy source parquet can be minutely or hourly. Both formats normalize
into `input_prices`. Price columns remain in the input schema for pipeline
compatibility and are not used for edge acceptance. Out-of-range prices are not
range-gated by the structural builder. Discovery also accepts compact market
snapshots directly.

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

## Discovery Pipeline

1. Detect a compact market snapshot or OddsFox minutely/hourly export.
2. Drop source rows that cannot identify propositions, then deterministically
   select markets by `volume DESC, market_id` within `max_propositions`.
3. Parse outcomes with the Pydantic structured-output contract. Normalize
   Unicode/case, exact generic aliases, units, and dates while preserving
   original values.
4. Embed canonical proposition text locally with the pinned
   `sentence-transformers/all-MiniLM-L6-v2` revision and retain each
   proposition's stable top-k neighbors.
5. Canonicalize unordered candidate pairs and retain all structural and
   semantic reasons.
6. Apply same-market, equivalence, numeric threshold, time containment,
   tournament stage, and single-winner deterministic rules.
7. Classify only prioritized unresolved pairs with bounded asynchronous
   `responses.parse` calls.
8. Enforce confidence/review gates and global consistency. Conflicting
   deterministic proofs fail; LLM conflicts enter the review queue.
9. Stage, validate, sort, and atomically publish artifacts. Write
   `build_manifest.json` last.

Parse and classification requests use content-addressed JSON cache entries
covering task, canonical input, requested model, reasoning setting, prompt, and
schema. `--offline` reads the same pipeline but fails on any cache miss and
never needs an API key.

## Major Tables And Views

- `input_prices`: normalized source rows.
- `token_minute_prices`: one latest row per `(clob_token_id, odds_minute_epoch)`.
- `market_token_counts`: expected token cardinality per market.
- `token_stats`: one identity row per token.
- `nodes_v`: proposition nodes with taxonomy fields.
- `candidate_edges_v`: structural candidates before acceptance.
- `logic_edges_v`: accepted logic edges.
- `conditional_edges_v`: exact 0/1 conditionals.
- `propositions_v`: parsed and canonical outcome propositions.
- `relation_candidates_v`: canonical candidate pairs and disposition.
- `review_queue_v`: parse, classification, and consistency exceptions.

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

Discovery adds parse-backed deterministic candidates and local embedding
neighbors. Embeddings only retrieve candidates; they never accept edges.
Symmetric relations are stored once in stable ID order. `equivalent` behaves
bidirectionally internally, `complement` is stronger than exclusion, and
directional classifications normalize to one `implies` orientation.

## Conditionals

Conditionals are derived only from accepted logic edges:

- complement / mutually exclusive → exact 0 both directions
- equivalent → exact 1 both directions
- implies → exact `P(dst|src) = 1`

No Frechet bounds and no price-ratio implication reverse rows are written.
`compatible` is an accepted graph relation but deliberately produces no
conditional row. `unrelated` and `uncertain` are never published as edges.
