# CLI

Current commands:

- pipeline: `doctor`, `qualify`, `discover`, `release-validate`, `run-summary`;
- runtime: `model-manifest`, `model-check`;
- explorer: `serve`, `explorer-export`;
- graph: `search`, `nodes`, `edges`, `condition`, `explain`, `explain-edge`,
  `prove`, `why-not`.

`discover`, `qualify`, and `doctor` use the compact input, cache, two model
manifests/endpoints, and compute profile. Shared runtime flags are
`--primary-model-manifest`, `--verifier-model-manifest`, `--primary-base-url`,
`--verifier-base-url`, and `--allow-remote-inference`.

Discovery accepts exactly one of `--max-propositions N` and `--all-propositions`.
The latter selects every complete market in the input. Use
`--classification-coverage-target` and `--max-visible-coverage-gap` to make
publication fail when the bounded classifier did not cover enough eligible pairs.
`--progress-format auto|plain|json|quiet` writes progress to stderr. Final command
results use `--output-format`.

`serve --out OUTPUT` starts a read-only loopback service. `--host` rejects
non-loopback addresses; `--port`, `--max-response-nodes`, and
`--max-response-edges` are validated. `--open-browser` is optional.

`explorer-export --out OUTPUT --destination DIRECTORY --scope
event|component|neighborhood --identifier ID` creates a deterministic static
snapshot. It fails rather than silently truncating the requested scope.

Every graph query accepts `--output-format table|json|jsonl`. `prove` takes
`--from`, `--to`, `--max-hops`, and `--max-paths`; traversal is capped at eight
hops, twenty returned paths, and a bounded number of generated search states.
`why-not` distinguishes
accepted, solver-rejected, quarantined parse, disagreement, assumption, invalid
citation, NLI veto, threshold, inference, retrieval, and unknown-node states.
