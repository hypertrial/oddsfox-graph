# Artifacts

Public graph data:

- `nodes.parquet`, `market_groups.parquet`, `propositions.parquet`;
- `relation_candidates.parquet` with retrieval, NLI, both model assessments,
  consensus status, fingerprints, and automation profile ID;
- `logic_edges.parquet` with deterministic or `generative_consensus` provenance;
- `rejected_edges.parquet` with solver conflicts and named constraints;
- `conditional_edges.parquet`, which excludes compatible relations;
- `parse_assessments.parquet`, one row per proposition and model role with the
  parsed payload, source-field citations, confidence, validation status, model,
  and fingerprint;
- `model_assessments.parquet`, one row per candidate and model role, including
  explicit `not_required` and `not_selected` rows for unclassified candidates;
- `quarantined_pairs.parquet` with machine-readable reason codes;
- `qualification_cases.parquet` with deterministic truth and partition bindings;
- `parse_errors.parquet` for invalid structured parse responses.

The output also contains `oddsfox_graph.duckdb`, `graph_snapshot.json`, Markdown
reports, `primary_model_manifest.json`, `verifier_model_manifest.json`,
`automation_profile.json`, `qualification_report.json`, optional
`compute_profile.json`, and incremental Parquet state under `state/`.

`build_manifest.json` is written last. It binds input and artifact hashes, model
and runtime identities, role/consensus fingerprints, qualification status,
protocol and state versions, limits, cache integrity/statistics, token usage,
compute accounting, solver/rule metadata, stage timings, RSS, and incremental
reuse.

Quarantine is diagnostic only. Its rows never enter graph publication,
conditional derivation, solver acceptance, or proof traversal.

## Core graph schemas

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
  `rule_version`, `prompt_version`, `explanation`, `assumptions`, `rule_id`,
  `proposal_id`, `solver_version`, `constraint_version`,
  `solver_component_id`, `primary_model_version`, `verifier_model_version`,
  `primary_assessment_id`, `verifier_assessment_id`,
  `primary_inference_fingerprint`, `verifier_inference_fingerprint`,
  `consensus_fingerprint`, `automation_profile_id`.
- `conditional_edges.parquet`: `a_node_id`, `b_node_id`, `p_a_given_b`,
  `method`, `confidence`, `evidence`.

The Markdown reports are `summary.md`, `strongest_implications.md`,
`strongest_exclusions.md`, `duplicate_edges.md`, `coverage.md`, and
`conditional_examples.md` under `reports/`.
