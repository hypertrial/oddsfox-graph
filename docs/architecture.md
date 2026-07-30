# Architecture

`oddsfox-graph` has two publication paths. The legacy `build` path is a
sequential DuckDB build using the WC2026 taxonomy. The `discover` path uses
Python for typed parsing, bounded model calls, and component consistency
solving, with a disk-backed DuckDB workspace for candidate retrieval and
deterministic artifact publication.

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
2. Validate the full eligible catalog, select lightweight market summaries by
   `volume DESC, market_id` within `max_propositions`, then fetch full arrays
   only for the retained market IDs.
3. Parse outcomes and full market descriptions with the Pydantic
   structured-output contract. Normalize
   Unicode/case, exact generic aliases, units, and dates while preserving
   original values.
4. Embed canonical proposition text locally with the pinned
   `sentence-transformers/all-MiniLM-L6-v2` revision. Compute exact cosine
   similarity in deterministic blocks and retain each proposition's stable
   top-k neighbors; vectors are cached by normalized-text hash.
5. Persist structural block memberships and reason contributions in a temporary
   DuckDB workspace. Bound oversized block membership, reserve every
   deterministic proof, aggregate reasons relationally, and keep the stable
   capped candidate set on disk. Python reads only deterministic proposals and
   the bounded classifier slice.
6. Always enable same-market hard facts. Apply equivalence, interval-set numeric,
   time containment, tournament-stage, and single-winner rules from the
   versioned rule registry.
7. Classify only prioritized unresolved pairs with bounded asynchronous
   `responses.parse` calls. Directional entailment and supporting-field
   citations must agree with the final relation, and every positive
   classification must cite at least one nonempty supplied field.
8. Apply per-relation thresholds, then solve independent proposal-connected
   components with deterministic PySAT RC2 clauses. Same-market facts are hard;
   other proposals are confidence-weighted soft facts. Rejected proposals carry
   conflict explanations.
9. Evaluate against a source-hash-bound human benchmark when supplied.
10. Attach the candidate workspace, bulk-bind the remaining bounded typed rows,
    stage, validate, sort, and atomically
    publish non-manifest artifacts and incremental state. Freeze run statistics,
    then write `build_manifest.json` last.

Parse and classification requests use content-addressed JSON cache entries
covering task, canonical input, requested model, reasoning setting, prompt, and
schema. Entries distinguish success, stable failure, and exhausted transient
failure. Offline mode replays any recorded terminal outcome and fails on a true
cache miss; online mode retries transient entries. Offline mode never needs an
API key.

## Discovery Modules

`oddsfox_graph.discovery` remains the public facade and orchestrator. Focused
implementation modules live under `oddsfox_graph._discovery`:

- `contracts`: Pydantic and source/configuration contracts
- `input`: schema detection, validation, selection, and normalization
- `cache`: versioned content-addressed entries and atomic storage
- `metrics`: monotonic timings and current/cached usage accounting
- `candidates`: blockwise embeddings and bounded DuckDB-backed reason aggregation
- `relations`: registered deterministic rule semantics
- `solver`: componentized, deterministic RC2 consistency selection
- `bulk`: typed chunked DuckDB list-of-struct insertion
- `workspace`: disk-backed candidate lifecycle, bounded reads, updates, and
  zero-rebind publication
- `incremental`: typed execution-plan records and affected-only verification
- `publication`: deterministic sorted parquet export helpers
- `evaluation_metrics`: relation calibration and scalar metric primitives
- `versions`: independent compatibility versions for every discovery stage

`oddsfox_graph.evaluation` owns benchmark export/compilation, domain taxonomy,
parser/retrieval/relation/calibration metrics, pricing, and deterministic exit
recommendations.

This keeps the legacy builder independent and avoids coupling OpenAI or local
embedding dependencies to DuckDB-only installations.

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
- `rejected_edges_v`: rejected positive proposals and solver conflicts.
- `parse_errors_v`: typed parser failures and low-confidence outcomes.

Incremental state is stored separately under `state/`: market/source and
proposition/parse fingerprints, reusable embedding vectors,
candidate-neighborhood fingerprints, structured block memberships and reason
rows, proposal/solver-component fingerprints, and `execution_plan.parquet`.
A baseline must be immutable, v0.5-compatible, complete, and distinct from the
new output directory.

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
neighbors. Embeddings only retrieve candidates; they never accept edges. A
deterministic rule publishes edges only when the compiled benchmark has at
least ten positive and ten adversarial examples for that stable rule ID;
otherwise it is reported as experimental. Same-market hard facts are exempt.
An explicit `--allow-unbenchmarked-rules` diagnostic override is recorded and
blocks `READY_TO_SCALE`.
Symmetric relations are stored once in stable ID order. `equivalent` behaves
bidirectionally internally, `complement` is stronger than exclusion, and
directional classifications normalize to one `implies` orientation.

## Benchmark And Release Decision

`benchmark-export` samples parse records and candidate, semantic near-miss,
structured near-miss, and noncandidate pairs from the top-5,000 population.
Reviewer files are blinded. `benchmark-compile` requires two distinct reviewer
aliases, nonempty notes, and adjudication for every disagreement; it binds the
truth to the supplied catalog SHA-256.

`evaluate` reports parser fields, entity sets, UTC dates, normalized
numeric/units, candidate recall, directional relation metrics, ECE, Brier
score, review/assumption rates, provenance, runtime, RSS, token usage, and
pricing-snapshot cost. It emits exactly one of `READY_TO_SCALE`,
`NEEDS_PARSER_WORK`, `NEEDS_RETRIEVAL_WORK`, or
`NEEDS_RELATION_MODEL_WORK`.

## Conditionals

Conditionals are derived only from accepted logic edges:

- complement / mutually exclusive → exact 0 both directions
- equivalent → exact 1 both directions
- implies → exact `P(dst|src) = 1`

No Frechet bounds and no price-ratio implication reverse rows are written.
`compatible` is an accepted graph relation but deliberately produces no
conditional row. `unrelated` and `uncertain` are never published as edges.
