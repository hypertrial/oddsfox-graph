# Artifacts

## Public graph

- `nodes.parquet`: `node_id`, `market_id`, `outcome_index`, `clob_token_id`,
  `question`, `outcome_label`, `event_slug`, `is_active`, `is_closed`,
  `market_family`, `canonical_proposition`, `proposition_type`,
  `expected_tokens`, `first_seen_ts`, `last_seen_ts`.
- `market_groups.parquet`: `market_id`, `event_slug`, `question`,
  `market_family`, `num_tokens`, `token_ids`, `outcome_labels`, `is_active`,
  `is_closed`, `first_seen_ts`, `last_seen_ts`.
- `conditional_edges.parquet`: `a_node_id`, `b_node_id`, `p_a_given_b`,
  `method`, `confidence`, `evidence`. Compatible relations are excluded.
- `propositions.parquet`, `relation_candidates.parquet`,
  `logic_edges.parquet`, and `rejected_edges.parquet`.

`logic_edges.parquet` contains `src_node_id`, `dst_node_id`, `edge_type`,
`edge_basis`, `confidence`, `market_id_src`, `market_id_dst`, `event_slug_src`,
`event_slug_dst`, `evidence`, `discovery_method`, `rule_version`,
`prompt_version`, `explanation`, `assumptions`, `rule_id`, `proposal_id`,
`solver_version`, `constraint_version`, `solver_component_id`,
`primary_model_version`, `verifier_model_version`, `primary_assessment_id`,
`verifier_assessment_id`, `primary_inference_fingerprint`,
`verifier_inference_fingerprint`, `consensus_fingerprint`,
`automation_profile_id`, `evidence_tier`, `extractor_id`, `extractor_version`,
`source_spans_json`, `rule_applicability_fingerprint`, and `proof_scope_key`.
Discovery methods are `deterministic` and `generative_consensus`; evidence tiers
are `source_contract`, `deterministic_rule`, and `generative_consensus`.

## Assessments and diagnostics

- `parse_assessments.parquet`: one row per proposition/assessor with
  `assessor_type=deterministic_extractor|primary_model|verifier_model`; model
  fields are nullable for deterministic rows.
- `model_assessments.parquet`: both model-role atomic judgments for selected
  candidates.
- `quarantined_pairs.parquet`: pre-solver failure or cutoff reasons.
- `parse_errors.parquet`: response, schema, omission, validation, and confidence
  failures.
- `qualification_cases.parquet`: exact generated cases bound to a full profile;
  fast emits a schema-valid empty file.

Fast likewise emits empty schema-valid model-assessment and quarantine files and
does not fabricate model manifests, compute profiles, or automation profiles.

## Explorer and state

`event_summary.parquet`, `event_relation_summary.parquet`,
`component_summary.parquet`, `node_metrics.parquet`, and
`visualization_layout.parquet` support bounded visualization. Event relation
summaries include `evidence_tier`; aggregate edges are navigation summaries, not
new logical facts. `coverage_summary.json` and `viewer_manifest.json` record the
mode, validation status, content fingerprint, limits, and coverage.

The output also contains `oddsfox_graph.duckdb`, `graph_snapshot.json`, sorted
Parquet state under `state/`, and reports `summary.md`,
`strongest_implications.md`, `strongest_exclusions.md`, `duplicate_edges.md`,
`coverage.md`, and `conditional_examples.md`. Full outputs add exact primary and
verifier manifests, automation/qualification files, and a compute profile.

`build_manifest.json` is the completion marker and is written last. It binds the
input, mode, validation status, public/state hashes, versions, evidence, rules,
solver, deadline, cutoff, resource use, cache, and incremental execution plan.
Fast manifests also bind every published database, snapshot, report, public
artifact, and state file in `published_file_hashes`; incremental reuse rejects
missing files or any content-hash mismatch before copying the baseline.
