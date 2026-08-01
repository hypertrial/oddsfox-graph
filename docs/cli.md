# CLI

Current commands:

- pipeline: `doctor`, `qualify`, `discover`, `release-validate`, `run-summary`;
- runtime: `model-manifest`, `model-check`;
- explorer: `serve`, `explorer-export`;
- graph: `search`, `nodes`, `edges`, `condition`, `explain`, `explain-edge`,
  `prove`, and `why-not`.

`discover --mode fast` needs only `--input` and `--out`. It uses the complete
catalog unless `--max-propositions N` is set. Fast supports
`--incremental-from`, `--deadline-seconds`, `--progress-format`, and
`--output-format`, and rejects all semantic-inference options.

`discover --mode full` additionally requires `--cache-dir`,
`--automation-profile`, `--primary-model-manifest`,
`--verifier-model-manifest`, `--primary-base-url`, `--verifier-base-url`, and
`--compute-profile`. Existing `--classification-coverage-target`,
`--max-visible-coverage-gap`, confidence thresholds, candidate/NLI limits, and
model-generation settings remain full-only controls. `--allow-remote-inference`
is explicit; URLs with credentials, queries, or fragments are rejected.

`doctor --mode fast|full` checks only resources needed by that mode. `qualify`
remains out of band and creates the exact full-mode automation profile; its time
is not part of the full discovery deadline. `--progress-format
auto|plain|json|quiet` writes events to stderr. Final results use
`--output-format table|json|jsonl` where supported.

`serve --out OUTPUT` starts a read-only loopback service. `explorer-export
--out OUTPUT --destination DIRECTORY --scope event|component|neighborhood
--identifier ID` creates a bounded portable snapshot and fails on truncation.

All graph queries accept table, JSON, or JSONL. `prove` takes `--from`, `--to`,
`--max-hops`, and `--max-paths`. `why-not` includes accepted, solver-rejected,
parse/model/citation/assumption/NLI/threshold failures,
`not_applicable_to_deterministic_rules`, `full_mode_not_run`,
`deadline_budget_exhausted`, `not_retrieved`, and `unknown_node`.
