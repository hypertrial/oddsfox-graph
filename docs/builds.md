# Builds

v0.5.0 preserves the offline structural builder and turns automated logical
discovery into a benchmark-driven, component-incremental pipeline with
blockwise retrieval and RC2 consistency solving.

## Legacy Command

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

## Discovery Command

```bash
python -m oddsfox_graph.cli discover \
  --input <parquet> \
  --out <dir> \
  --cache-dir <dir>
```

Compact inputs require `market_id`, `question`, and equal-length nonempty
`outcomes` / `clob_token_ids` arrays whose elements are also nonempty. Optional
fields include `description`, `event_id`, `event_slug`, `category`, `tags`,
`volume`, `start_time`, and `end_time`.
OddsFox minutely/hourly exports are deduplicated to one proposition per distinct
token.

When a catalog exceeds `max_propositions`, discovery selects complete markets
by descending `volume` with `market_id` as a stable tie-breaker. It records
input, eligible, invalid, and selected counts in `stats.input_selection`.

Live requests use OpenAI Responses structured parsing with `store=False`,
bounded concurrency, transient retries, and a content-addressed JSON cache.
The cache key includes task, canonical input, model, reasoning setting, prompt,
and schema hashes. Versioned entries record `success`, `stable_failure`, or
`transient_failure`. Online runs retry cached transient failures. `--offline`
requires complete cache or reusable state coverage, replays recorded terminal
failures to the review queue, and never reads `OPENAI_API_KEY`.

Local cosine retrieval processes normalized embeddings in deterministic blocks
instead of allocating a full similarity matrix. Deterministic and classified
proposals are solved in independent RC2 components. Same-market facts are hard
clauses; other proposals are weighted soft clauses. Rejected positive proposals
retain the selected conflicts and named constraints that exclude them.

`--incremental-from` requires a distinct, manifest-complete v0.5 baseline.
Older candidate state is rejected with clean-build guidance. Source,
parser, normalization, embedding, rule, classifier, threshold, and solver
versions drive stage-specific reuse. State is an implementation detail under
`state/`; the public logical artifacts must match a clean run byte-for-byte in
their canonical content. An unchanged candidate population is copied from the
baseline; partial changes recompute candidate neighborhoods while reusing
unchanged normalized vectors and classifications only when the complete typed
pair evidence remains identical. Solver
proposal components are reused independently by fingerprint. Structured
candidate contributions are persisted per versioned block; an incremental run
copies unchanged block rows exactly and regenerates changed blocks before the
required global cap and priority aggregation. Changing `--max-candidates`
invalidates those contributions because it also changes the per-block
membership bound; changing the benchmark-enabled rule set invalidates the
capped candidate population. For changed or
added propositions, semantic retrieval scores changed-to-all; an unchanged
proposition is rescored globally only when a changed or removed node occupied
its prior top-k.

## Manifest

Successful builds write `build_manifest.json` last. Fields:

| Field | Meaning |
|---|---|
| `command` | `discover` for automated-discovery manifests |
| `version` | Package version that produced discovery artifacts |
| `input` | Source parquet path |
| `input_hash` | SHA-256 of the source parquet |
| `input_format` | `market_snapshot`, `minutely`, or `hourly` |
| `input_granularity_seconds` | Source bucket size in seconds |
| `taxonomy` | Object with `name`, `path`, and `hash` |
| `models` | Requested/observed parse and classify models plus embedding revision |
| `prompts` | Parse/classify prompt versions and hashes |
| `versions` | Independent normalization, taxonomy, retrieval, rules, cache, solver, and state versions |
| `benchmark` | Compiled benchmark path/hash/source binding and rule-gate status |
| `pricing` | Pricing snapshot path/hash used for reproducible cost estimates |
| `solver` | Solver/encoding versions, objectives, components, clauses, and proposals |
| `rules` | Registry version and enabled/experimental rule evidence |
| `limits` | Per-relation confidence, retrieval block size, proposition, candidate, LLM, and concurrency limits |
| `cache` | Directory, mode, state-specific hits, misses, transient retries, and writes |
| `usage` | Current-request, cached-origin, accounted-total, and per-task token usage |
| `artifacts` | Published parquet/json artifact names |
| `artifact_hashes` | SHA-256 hashes of deterministic logical parquet artifacts |
| `state_hashes` | SHA-256 hashes of implementation-only incremental state |
| `incremental` | Baseline, changed/removed/reused units, and invalidation reasons |
| `reports` | Markdown report paths under `reports/` |
| `stats` | Row counts and runtime |
| `stage_timings` | Per-stage seconds |

Discovery `stats.input_selection` records `input_rows`, `input_propositions`,
`invalid_market_rows`, `eligible_markets`, `eligible_propositions`,
`selected_markets`, `selected_propositions`, selection `strategy`, and whether
the catalog was `truncated`.

`state/execution_plan.parquet` is the auditable execution proof. It records
market, semantic-neighborhood, structured-block, solver-component, and required
global-stage decisions with input/output fingerprints and invalidation reasons.
Evaluation reads and verifies this artifact and its logical hash; reused-count
claims in the manifest alone cannot satisfy the incremental release gate.

## Scratch Database

The legacy `build` command writes `oddsfox_graph.duckdb` under `--out` as a
working database. Discovery uses an equivalent staging-only database and does
not publish it. Neither database is a contract artifact.

## Publication And Reproducibility

Discovery writes into a temporary staging directory, validates schemas, counts,
edge invariants, and deterministic ordering, then atomically replaces each
non-manifest artifact and state file. It records input, logical-artifact, state,
benchmark, pricing, and component hashes, then writes the manifest as the last
completion marker. A failure
after artifact publication but before the manifest leaves no false completion
marker. Returned statistics exactly match `manifest.stats`.

A cache-complete offline rerun must reproduce every logical parquet hash.
Current-run usage is zero offline; originating request usage remains available
under `usage.cached_origin` and the combined accounting under
`usage.accounted_total`. Legacy v1 entries remain readable, but their duplicated
per-item batch usage is deliberately excluded rather than reported as an
inflated total; `cache.legacy_hits` exposes those reads.

## Release Validation

Ordinary CI uses lightweight fakes and performs no OpenAI request or embedding
download. The protected manual workflow requires a
`discovery-release-fixture` artifact containing:

- `input.parquet`
- `cache/`
- `baselines/5000/` and `baselines/20000/`, including `state/`
- `expected-artifact-hashes.json` keyed by `5000` and `20000`
- `benchmark.parquet`
- `pricing.json`
- `fixture-manifest.json`, conforming to
  [`release-fixture-manifest.schema.json`](release-fixture-manifest.schema.json)

Missing or provenance-mismatched inputs fail the workflow. The fixture manifest
binds every critical file and the cache/baseline trees. Validation verifies the
input hash, runs 5,000- and
20,000-proposition incremental/offline discovery, compares online/offline and
clean/incremental logical hashes, requires `READY_TO_SCALE`, builds the wheel,
and uploads the complete validation record. Genuine human labels, notes,
adjudication, complete caches, model access, and the M4 runs remain external
release prerequisites.

Generate the provenance file only after the fixture is immutable:

```bash
python scripts/create_release_fixture_manifest.py \
  --fixture-root <release-fixture>
```

`oddsfox_graph.duckdb` is an implementation scratch file. Consumers should use
the published parquet, reports, snapshot, and manifest.

## Legacy Omitted Surfaces

The following remain intentionally absent from the legacy builder:

- prices / price-only edges
- calibration / coherence / violations / evaluation
- knockout artifacts
- candidate-edge parquet exports

Rebuilds into an existing output directory also delete those legacy artifact and
report names so stale v0.1 files cannot remain beside the structural set.
