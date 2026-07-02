# Artifact Reference

The graph node id is always `clob_token_id`, exposed as `node_id`.
`market_id` is a market container, not the graph node. Successful builds write
`build_manifest.json` last; use the manifest artifact list as the contract for
that output directory.

Optional artifacts can be absent by design. `prices.parquet` is omitted by
`--skip-prices` and `--fast-graph`. `coherence.parquet` and
`coherence_repairs.parquet` are omitted by `--skip-coherence` and
`--fast-graph`. `evaluation.parquet` is written only when `--resolutions` is
provided.

Build-mode behavior is documented in [Build Modes](builds.md). Pipeline behavior
is documented in [Architecture](architecture.md).

## Parquet Artifacts

### `nodes.parquet`

Grain: one row per `clob_token_id`.

Purpose: canonical proposition table for graph nodes.

Columns: `node_id`, `market_id`, `outcome_index`, `clob_token_id`, `question`,
`outcome_label`, `event_slug`, `is_active`, `is_closed`, `market_volume_usd`,
`market_family`, `canonical_proposition`, `proposition_type`,
`expected_tokens`, `first_seen_ts`, `last_seen_ts`, `active_minutes`,
`current_price`, `current_price_devig`, `mean_price`, `mean_price_devig`,
`min_price`, `max_price`.

### `prices.parquet`

Grain: one row per `(node_id, odds_minute_epoch)` after deduplication.

Purpose: minute-level price series with devig and scoring prices.

Columns: `node_id`, `market_id`, `odds_timestamp`, `odds_timestamp_epoch`,
`price`, `price_devig`, `scoring_price`, `is_active`, `is_closed`,
`market_volume_usd`, `logit_price`, `price_return_1m`.

### `market_groups.parquet`

Grain: one row per `market_id`.

Purpose: market-level grouping and sum diagnostics for binary and n-ary markets.

Columns: `market_id`, `event_slug`, `question`, `market_family`, `num_tokens`,
`token_ids`, `outcome_labels`, `is_active`, `is_closed`, `market_volume_usd`,
`first_seen_ts`, `last_seen_ts`, `current_sum_price`, `mean_sum_price`.

### `candidate_edges.parquet`

Grain: one row per `(src_node_id, dst_node_id, candidate_type)`.

Purpose: candidate relationships before acceptance filters.

Columns: `src_node_id`, `dst_node_id`, `candidate_type`, `candidate_source`,
`candidate_score`, `market_id_src`, `market_id_dst`, `event_slug_src`,
`event_slug_dst`.

Candidate sources include `same_market`, `exact_duplicate_same_event`,
`semantic_single_winner`, `semantic_stage_progression`, and
`price_same_event_slug`.

### `logic_edges.parquet`

Grain: one row per accepted strict semantic or structural graph edge.

Purpose: trusted relationships for graph logic.

Columns: `src_node_id`, `dst_node_id`, `edge_type`, `edge_basis`, `confidence`,
`score`, `violation_score`, `overlap_minutes`, `current_p_src`,
`current_p_dst`, `mean_p_src`, `mean_p_dst`, `market_id_src`, `market_id_dst`,
`event_slug_src`, `event_slug_dst`, `evidence`.

### `price_edges.parquet`

Grain: one row per price-threshold relationship that was not promoted to logic.

Purpose: inspect useful price signals without treating them as structural graph
facts.

Columns: `src_node_id`, `dst_node_id`, `edge_type`, `edge_basis`, `confidence`,
`score`, `violation_score`, `overlap_minutes`, `current_p_src`,
`current_p_dst`, `mean_p_src`, `mean_p_dst`, `market_id_src`, `market_id_dst`,
`event_slug_src`, `event_slug_dst`, `evidence`.

### `derived_edges.parquet`

Grain: one row per derived implication.

Purpose: transitive closure of accepted `implies` logic edges.

Columns: `src_node_id`, `dst_node_id`, `edge_type`, `edge_basis`, `confidence`,
`path`, `evidence`.

### `constraint_hyperedges.parquet`

Grain: one row per market-level constraint.

Purpose: binary complement and one-of-n constraints for market groups.

Columns: `constraint_id`, `constraint_type`, `market_id`, `event_slug`,
`question`, `node_ids`, `current_sum_price`, `mean_sum_price`,
`expected_sum_price`, `violation_score`, `confidence`, `evidence`.

### `conditional_edges.parquet`

Grain: one row per ordered pair conditional emitted by the build.

Purpose: exact conditionals from logic/derived edges and bounded estimates for
unrelated pairs.

Columns: `a_node_id`, `b_node_id`, `p_a_given_b`, `lower_bound`,
`upper_bound`, `method`, `confidence`, `as_of_ts`, `evidence`.

### `violations.parquet`

Grain: one row per persistence-aware pricing, logic, or global incoherence
violation.

Purpose: current contradictions that persisted long enough to report.

Columns: `violation_id`, `violation_type`, `src_node_id`, `dst_node_id`,
`market_id_src`, `market_id_dst`, `event_slug_src`, `event_slug_dst`,
`severity`, `current_gap`, `mean_gap`, `confidence`, `first_seen_ts`,
`last_seen_ts`, `description`.

### `calibration.parquet`

Grain: one row per liquidity bucket.

Purpose: empirical complement-noise buckets and derived threshold quantiles.

Columns: `bucket_id`, `volume_min`, `volume_max`, `sample_count`,
`complement_p50`, `complement_p95`, `equivalence_p95`, `implication_p95`,
`exclusion_p95`.

### `coherence.parquet`

Grain: one row per `event_slug`.

Purpose: event-level LP repair summary.

Columns: `event_slug`, `node_count`, `constraint_count`,
`incoherence_distance`, `solver_status`.

### `coherence_repairs.parquet`

Grain: one row per repaired node in each event.

Purpose: observed and repaired prices from the event-level LP solve.

Columns: `event_slug`, `node_id`, `observed_price`, `repaired_price`,
`adjustment`.

### `evaluation.parquet`

Grain: one row per evaluation metric bucket.

Purpose: optional resolution backtest metrics.

Columns: `metric_type`, `artifact`, `edge_basis`, `edge_type`,
`violation_type`, `liquidity_bucket`, `edge_count`, `value`.

## Markdown Reports

Reports are written under `reports/`.

- `summary.md`: summary counts and build stats.
- `top_complement_violations.md`: largest complement violations.
- `strongest_implications.md`: highest-confidence implication edges.
- `strongest_exclusions.md`: highest-confidence mutual exclusion edges.
- `duplicate_candidates.md`: duplicate proposition candidates.
- `price_only_edges.md`: strongest price-only edges.
- `coverage.md`: market-family and edge-basis coverage.
- `conditional_examples.md`: sample conditional probability rows.
- `evaluation.md`: optional evaluation report written with `--resolutions`.

## Manifest

`build_manifest.json` records input paths, taxonomy metadata, effective
thresholds, LP warnings, build options, generated artifacts, generated reports,
summary stats, and stage timings. The manifest shape and omission semantics are
documented in [Build Modes](builds.md).
