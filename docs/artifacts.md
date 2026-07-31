# Artifacts

## Graph and inference

- `nodes.parquet`, `market_groups.parquet`, `propositions.parquet`;
- `relation_candidates.parquet`, `logic_edges.parquet`,
  `conditional_edges.parquet`, and `rejected_edges.parquet`;
- `parse_assessments.parquet`, `model_assessments.parquet`,
  `quarantined_pairs.parquet`, `qualification_cases.parquet`, and
  `parse_errors.parquet`.

`logic_edges.parquet` contains `src_node_id`, `dst_node_id`, `edge_type`,
`edge_basis`, `confidence`, source/destination market and event fields,
`evidence`, `discovery_method`, rule/prompt/solver provenance, `proposal_id`,
`primary_model_version`, `verifier_model_version`,
`primary_inference_fingerprint`, `verifier_inference_fingerprint`,
`consensus_fingerprint`, and `automation_profile_id`. Accepted methods are
`deterministic` and `generative_consensus`.

`nodes.parquet` contains `node_id`, `market_id`, `outcome_index`,
`clob_token_id`, `question`, `outcome_label`, `event_slug`, `is_active`,
`is_closed`, `market_family`, `canonical_proposition`, `proposition_type`,
`expected_tokens`, `first_seen_ts`, and `last_seen_ts`.

`market_groups.parquet` contains `market_id`, `event_slug`, `question`,
`market_family`, `num_tokens`, `token_ids`, `outcome_labels`, `is_active`,
`is_closed`, `first_seen_ts`, and `last_seen_ts`.

The complete edge provenance names are `market_id_src`, `market_id_dst`,
`event_slug_src`, `event_slug_dst`, `rule_version`, `prompt_version`,
`explanation`, `assumptions`, `rule_id`, `solver_version`,
`constraint_version`, `solver_component_id`, `primary_assessment_id`, and
`verifier_assessment_id`.

`conditional_edges.parquet` contains `a_node_id`, `b_node_id`, `p_a_given_b`,
`method`, `confidence`, and `evidence`; compatible relations are excluded.

## Explorer

- `event_summary.parquet`: event identity/domain, market and proposition counts,
  accepted/rejected/quarantined/unclassified counts, classification eligibility,
  assessment and coverage, per-relation counts, components, and time bounds;
- `event_relation_summary.parquet`: directed event pair, relation, counts,
  confidence range/mean, provenance counts, touched markets, and the explicit
  `aggregation_only` marker;
- `component_summary.parquet`: stable component identity/fingerprint, node,
  market, event, edge, quarantine, unclassified, coverage, representative nodes,
  and layout bounds;
- `node_metrics.parquet`: `node_id`, `market_id`, `event_key`, `component_id`,
  directional/per-relation degree, rejection/quarantine counts, parse status, and
  classification state, eligible/assessed/unclassified counts, and
  `classification_coverage`;
- `visualization_layout.parquet`: level, object, parent, coordinates, radius,
  stable rank, layout version, and graph fingerprint;
- `coverage_summary.json`: full-selection flag and all run-level coverage counts;
- `viewer_manifest.json`: viewer/API/layout contract versions, source watermark,
  graph content fingerprint, and response ceilings.

Event relations are summaries for visualization, never additional logical facts.
Proofs and conditionals use proposition-level accepted edges only.

## Completion and supporting data

The output also contains `oddsfox_graph.duckdb`, `graph_snapshot.json`, model
manifests, automation/qualification reports, optional compute profile, incremental
state under `state/`, and Markdown reports under `reports/`.
The reports are `summary.md`, `strongest_implications.md`,
`strongest_exclusions.md`, `duplicate_edges.md`, `coverage.md`, and
`conditional_examples.md`.

`build_manifest.json` is written last and binds every public artifact hash,
viewer version, input selection, model/runtime identity, cache statistics,
qualification, limits, timings, resource use, solver/rule metadata, and
incremental reuse. Quarantine is diagnostic only and never enters publication,
conditional derivation, or proof traversal.
