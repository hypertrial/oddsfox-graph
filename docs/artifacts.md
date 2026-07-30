# Artifact Reference

The graph node id is always `clob_token_id`, exposed as `node_id`.
`market_id` is a market container, not the graph node. Successful builds write
`build_manifest.json` last; use the manifest artifact list as the contract for
that output directory.

## Portable JSON

### `graph_snapshot.json`

Purpose: compact portable structural snapshot.

Top-level fields: `version`, `built_at`, `source_manifest`, `counts`, `nodes`,
`logic_edges`, and `conditionals`.

Nodes expose `node_id`, `market_id`, `question`, `outcome_label`,
`canonical_proposition`, `team`, and `stage_key`. Logic edges expose `source`,
`target`, `type`, `basis`, and `confidence`. Conditionals expose `a_node_id`,
`b_node_id`, `p_a_given_b`, `method`, and `confidence`.

## Parquet Artifacts

### `nodes.parquet`

Grain: one row per `clob_token_id`.

Columns: `node_id`, `market_id`, `outcome_index`, `clob_token_id`, `question`,
`outcome_label`, `event_slug`, `is_active`, `is_closed`, `market_family`,
`canonical_proposition`, `proposition_type`, `expected_tokens`,
`first_seen_ts`, `last_seen_ts`.

### `market_groups.parquet`

Grain: one row per `market_id`.

Columns: `market_id`, `event_slug`, `question`, `market_family`, `num_tokens`,
`token_ids`, `outcome_labels`, `is_active`, `is_closed`, `first_seen_ts`,
`last_seen_ts`.

### `logic_edges.parquet`

Grain: one accepted structural/semantic edge.

Columns: `src_node_id`, `dst_node_id`, `edge_type`, `edge_basis`, `confidence`,
`market_id_src`, `market_id_dst`, `event_slug_src`, `event_slug_dst`,
`evidence`.

Accepted `edge_basis` values:

- `same_market`
- `exact_duplicate`
- `single_winner_family`
- `stage_progression_rule`

### `conditional_edges.parquet`

Grain: one exact logic-only conditional.

Columns: `a_node_id`, `b_node_id`, `p_a_given_b`, `method`, `confidence`,
`evidence`.

Methods:

- `exact_complement` / `exact_exclusion` → `p_a_given_b = 0`
- `exact_equivalence` → `p_a_given_b = 1`
- `exact_implication` → `P(dst|src) = 1` for `implies` edges

Price-ratio and Frechet methods are not produced.

## Reports

Reports are written under `reports/`:

- `summary.md`
- `strongest_implications.md`
- `strongest_exclusions.md`
- `duplicate_edges.md`
- `coverage.md`
- `conditional_examples.md`
