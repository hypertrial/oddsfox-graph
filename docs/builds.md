# Builds

v0.3.0 preserves the offline structural builder and adds automated logical
discovery.

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
`outcomes` / `clob_token_ids` arrays. Optional fields include `event_id`,
`event_slug`, `category`, `tags`, `volume`, `start_time`, and `end_time`.
OddsFox minutely/hourly exports are deduplicated to one proposition per distinct
token.

When a catalog exceeds `max_propositions`, discovery selects complete markets
by descending `volume` with `market_id` as a stable tie-breaker. It records
input, eligible, invalid, and selected counts in `stats.input_selection`.

Live requests use OpenAI Responses structured parsing with `store=False`,
bounded concurrency, transient retries, and a content-addressed JSON cache.
The cache key includes task, canonical input, model, reasoning setting, prompt,
and schema hashes. `--offline` requires complete parse and classification cache
coverage; it never reads `OPENAI_API_KEY`.

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
| `limits` | Confidence, retrieval, proposition, candidate, LLM, and concurrency limits |
| `cache` | Directory, mode, hit, miss, and write counts |
| `usage` | Returned input, output, and total token usage |
| `artifacts` | Published parquet/json artifact names |
| `artifact_hashes` | SHA-256 hashes of deterministic logical parquet artifacts |
| `reports` | Markdown report paths under `reports/` |
| `stats` | Row counts and runtime |
| `stage_timings` | Per-stage seconds |

Discovery `stats.input_selection` records `input_rows`, `input_propositions`,
`invalid_market_rows`, `eligible_markets`, `eligible_propositions`,
`selected_markets`, `selected_propositions`, selection `strategy`, and whether
the catalog was `truncated`.

## Scratch Database

Builds also write `oddsfox_graph.duckdb` under `--out` as a working database.
It is cleared on rebuild and is not a published contract artifact.

## Publication And Reproducibility

Discovery writes into a temporary staging directory, validates schemas, counts,
edge invariants, and deterministic ordering, then publishes. The manifest is
the last completion marker. A cache-complete offline rerun must reproduce every
logical parquet hash; only manifest cache and timing metadata may differ.

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
