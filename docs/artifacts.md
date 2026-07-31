# Artifact Contracts

All Parquet files are deterministically sorted. Nullable structured fields are
present even when their value is null.

## Graph

- `nodes.parquet`: `node_id`, `market_id`, `outcome_index`, `clob_token_id`,
  `question`, `outcome_label`, `event_slug`, `is_active`, `is_closed`,
  `market_family`, `canonical_proposition`, `proposition_type`,
  `expected_tokens`, `first_seen_ts`, `last_seen_ts`.
- `market_groups.parquet`: `market_id`, `event_slug`, `question`,
  `market_family`, `num_tokens`, `token_ids`, `outcome_labels`, `is_active`,
  `is_closed`, `first_seen_ts`, `last_seen_ts`.
- `logic_edges.parquet`: `src_node_id`, `dst_node_id`, `edge_type`,
  `edge_basis`, `confidence`, `market_id_src`, `market_id_dst`,
  `event_slug_src`, `event_slug_dst`, `evidence`, `discovery_method`,
  `rule_version`, `model_version`, `prompt_version`, `explanation`,
  `assumptions`, `rule_id`, `proposal_id`, `solver_version`,
  `constraint_version`, `solver_component_id`, `inference_fingerprint`,
  `model_profile_id`.
- `conditional_edges.parquet`: `a_node_id`, `b_node_id`, `p_a_given_b`,
  `method`, `confidence`, `evidence`.

## Discovery

- `propositions.parquet`: `proposition_id`, `market_id`, `event_id`,
  `event_slug`, `clob_token_id`, `outcome_index`, `outcome`, `question`,
  `description`, `market_source_hash`, `normalization_version`, `category`,
  `tags`, `subject_original`, `subject`, `predicate`, `object_original`,
  `object`, `operator`, `threshold`, `unit_original`, `unit`, `time_start`,
  `time_end`, `competition_original`, `competition`, `event_scope_original`,
  `event_scope`, `jurisdiction_original`, `jurisdiction`, `polarity`,
  `parse_confidence`, `parse_status`, `parser_model`, `prompt_version`,
  `inference_fingerprint`, `model_profile_id`, `source_schema`.
- `relation_candidates.parquet`: `proposition_a_id`, `proposition_b_id`,
  `candidate_reasons`, `embedding_similarity`, `embedding_rank`,
  `deterministic_relation`, `rule_id`, `rule_status`,
  `classification_relation`, `classification_confidence`,
  `atomic_a_implies_b`, `atomic_b_implies_a`, `atomic_can_both_be_true`,
  `atomic_must_one_be_true`, `atomic_logically_related`, `supporting_fields`,
  `a_implies_b`, `b_implies_a`, `explanation`, `assumptions`,
  `requires_review`, `unsupported_assumption`, `nli_a_to_b_entailment`,
  `nli_a_to_b_contradiction`, `nli_a_to_b_neutral`,
  `nli_b_to_a_entailment`, `nli_b_to_a_contradiction`,
  `nli_b_to_a_neutral`, `nli_action`, `status`, `discovery_method`,
  `model_version`, `prompt_version`, `inference_fingerprint`,
  `model_profile_id`.
- `review_queue.parquet`: `review_id`, `proposition_a_id`,
  `proposition_b_id`, `review_kind`, `proposed_relation`, `confidence`,
  `explanation`, `assumptions`, `model_version`, `prompt_version`.
- `rejected_edges.parquet`: `proposal_id`, `src_node_id`, `dst_node_id`,
  `edge_type`, `edge_basis`, `confidence`, `discovery_method`, `rule_id`,
  `rule_version`, `model_version`, `prompt_version`, `rejection_reason`,
  `conflicting_proposal_ids`, `conflicting_constraint_ids`,
  `solver_component_id`.
- `parse_errors.parquet`: `error_id`, `proposition_id`, `market_id`,
  `error_kind`, `error_message`, `cache_state`, `error_type`, `status_code`,
  `response_json`, `question`, `description`, `parse_confidence`,
  `market_source_hash`, `parser_model`, `prompt_version`, `schema_version`,
  `normalization_version`.

`oddsfox_graph.duckdb` is the promoted, checkpointed discovery workspace and
contains the public graph plus retained state tables. `graph_snapshot.json`,
`model_manifest.json`, optional `model_profile.json`, optional
`compute_profile.json`, optional `benchmark.parquet`, and optional
`evaluation_report.json` accompany the Parquet files. The snapshot `built_at`
value is the latest source-data watermark (or the Unix epoch when the input has
no timestamps), keeping equivalent replays byte-stable.

## State, reports, and manifest

Incremental state is stored under `state/`. Reports are `summary.md`,
`strongest_implications.md`, `strongest_exclusions.md`, `duplicate_edges.md`,
`coverage.md`, and `conditional_examples.md`.

`build_manifest.json` records `command`, `version`, `input`, `input_hash`,
`input_schema`, `models`, `prompts`, `inference`, `versions`, `limits`,
`incremental`, `benchmark`, `compute`, `solver`, `rules`, `cache`, `usage`,
`artifacts`, `artifact_hashes`, `state_hashes`, `reports`, `stats`,
`stage_timings`, and `stage_metrics`. Cache metadata includes its SQLite
format, integrity result, database hash, entry/state counts, file bytes, and
bulk transaction statistics. The manifest is written last.
