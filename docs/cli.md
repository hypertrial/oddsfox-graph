# CLI

Current commands:

- pipeline: `doctor`, `qualify`, `discover`, `release-validate`, `run-summary`;
- runtime: `model-manifest`, `model-check`;
- explorer: `serve`, `explorer-export`, `record`;
- graph: `search`, `nodes`, `edges`, `condition`, `explain`, `explain-edge`,
  `prove`, and `why-not`.

`doctor`, `discover`, and `qualify` accept `--input-profile
auto|polymarket-market-snapshot-v1|polymarket-wc2026-graph-hourly-v1`.
Production explorer builds pass the WC2026 profile explicitly; `auto` succeeds
only when exactly one schema matches.

`discover --mode fast` needs only `--input` and `--out`. Generic compact input
uses the complete catalog unless `--max-propositions N` is set. WC2026 hourly
input rejects that option so team-stage chains cannot be truncated. Fast supports
`--incremental-from`, `--deadline-seconds`, `--progress-format`, and
`--output-format`, and rejects all semantic-inference options.
For `discover` and `qualify`, the output cannot be the input file or an ancestor
directory containing it.

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

Full-mode doctor treats a missing automation-profile file as a required-check
failure. `model-check` requires the endpoint to report the exact runtime version
and context length recorded in its manifest. Discovery deadlines must be
strictly positive; an explicit zero is rejected rather than replaced by a
default.

`serve --out OUTPUT` starts a read-only loopback service. `explorer-export
--out OUTPUT --destination DIRECTORY --scope graph` creates the complete
standalone WC2026 explorer. The existing
`event|component|neighborhood --identifier ID` scopes remain available for
technical snapshots and fail on truncation.

`record --out OUTPUT --destination NEW_DIRECTORY` automatically ranks logical
edges, freezes the bounded story graph and layout, renders every addressed
frame, and publishes a captioned H.264 MP4 bundle. `--highlights` accepts 1–12,
`--min-confidence` accepts 0–1, even `--width` accepts 640–3840, even
`--height` accepts 360–2160, and `--fps` accepts 24, 30, or 60. Recording
progress uses `--progress-format plain|json|quiet` on stderr; the final JSON
result remains on stdout. The new destination must not exist and must not
overlap the completed graph directory.

All graph queries accept table, JSON, or JSONL. `prove` takes `--from`, `--to`,
`--max-hops`, and `--max-paths`. `why-not` includes accepted, solver-rejected,
parse/model/citation/assumption/NLI/threshold failures,
`not_applicable_to_deterministic_rules`, `full_mode_not_run`,
`deadline_budget_exhausted`, `not_retrieved`, and `unknown_node`.
