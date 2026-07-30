# Artifact Reference

The graph node id is always `clob_token_id`, exposed as `node_id`.
`market_id` is a market container, not the graph node. Successful builds write
`build_manifest.json` last; use the manifest artifact list as the contract for
that output directory.

## Portable JSON

### `graph_snapshot.json`

Purpose: portable structural snapshot.

Top-level fields: `version`, `built_at`, `source_manifest`, `counts`, `nodes`,
`logic_edges`, and `conditionals`. The `version` field is `v` plus the package
`__version__`.

Nodes expose `node_id`, `market_id`, `question`, `outcome_label`,
`canonical_proposition`, `team`, and `stage_key`. Logic edges expose `source`,
`target`, `type`, `basis`, and `confidence`. Conditionals expose `a_node_id`,
`b_node_id`, `p_a_given_b`, `method`, and `confidence`.

## Scratch Database

`oddsfox_graph.duckdb` may appear under the build output directory as a working
database. It is cleared on rebuild and is not part of the published artifact
contract.

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
`evidence`, `discovery_method`, `rule_version`, `model_version`,
`prompt_version`, `explanation`, `assumptions`.

Accepted `edge_basis` values:

- `same_market`
- `exact_duplicate`
- `single_winner_family`
- `stage_progression_rule`

Discovery also uses `normalized_equivalence`, `numeric_threshold`,
`time_window_containment`, `tournament_stage`, `single_winner`, and
`llm_classifier`. `compatible` is a graph edge, but it is excluded from
conditional derivation. `unrelated` and `uncertain` are not edge types.

### `conditional_edges.parquet`

Grain: one exact logic-only conditional.

Columns: `a_node_id`, `b_node_id`, `p_a_given_b`, `method`, `confidence`,
`evidence`.

Methods:

- `exact_complement` / `exact_exclusion` → `p_a_given_b = 0`
- `exact_equivalence` → `p_a_given_b = 1`
- `exact_implication` → `P(dst|src) = 1` for `implies` edges

Price-ratio and Frechet methods are not produced.

### `propositions.parquet`

Discovery only. Grain: one row per outcome, with
`proposition_id = clob_token_id`.

Columns: `proposition_id`, `market_id`, `event_id`, `event_slug`,
`clob_token_id`, `outcome_index`, `outcome`, `question`, `category`, `tags`,
`subject_original`, `subject`, `predicate`, `object_original`, `object`,
`operator`, `threshold`, `unit_original`, `unit`, `time_start`, `time_end`,
`competition_original`, `competition`, `jurisdiction_original`,
`jurisdiction`, `polarity`, `parse_confidence`, `parse_status`, `parser_model`,
`prompt_version`, `source_format`.

Original/canonical pairs preserve normalization provenance. Nullable structured
fields remain present and may contain null.

### `relation_candidates.parquet`

Discovery only. Grain: one canonical unordered proposition pair.

Columns: `proposition_a_id`, `proposition_b_id`, `candidate_reasons`,
`embedding_similarity`, `embedding_rank`, `deterministic_relation`,
`classification_relation`, `classification_confidence`, `explanation`,
`assumptions`, `requires_review`, `status`, `discovery_method`,
`model_version`, `prompt_version`.

Candidate reasons may include shared entity, event, competition, predicate,
unit, overlapping dates, and embedding rank/score. Embedding-only candidates
cannot be accepted without classification.

### `review_queue.parquet`

Discovery only. Grain: one deduplicated review item.

Columns: `review_id`, `proposition_a_id`, `proposition_b_id`, `review_kind`,
`proposed_relation`, `confidence`, `explanation`, `assumptions`,
`model_version`, `prompt_version`.

The queue includes parse failures, low-confidence parses/classifications,
explicit review requests, refusals, malformed or exhausted requests, and LLM
consistency conflicts.

### `evaluation.json`

Written by `review-score`. It contains completed sample counts, deterministic
and overall precision, candidate recall, provenance failures, threshold
definitions, per-gate booleans, and the overall `passed` result.
Completed CSV labels must use a supported relation name (`equivalent`,
`implies`, `A_implies_B`, `B_implies_A`, `mutually_exclusive`, `complement`,
`compatible`, `unrelated`, or `uncertain`); unknown or missing labels fail
scoring.

## Reports

Reports are written under `reports/`:

- `summary.md`
- `strongest_implications.md`
- `strongest_exclusions.md`
- `duplicate_edges.md`
- `coverage.md`
- `conditional_examples.md`
