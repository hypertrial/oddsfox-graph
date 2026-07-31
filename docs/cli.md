# CLI

Current commands:

- pipeline: `doctor`, `qualify`, `discover`, `release-validate`, `run-summary`;
- runtime: `model-manifest`, `model-check`;
- graph: `search`, `nodes`, `edges`, `condition`, `explain`, `explain-edge`,
  `prove`, `why-not`.

`discover`, `qualify`, and `doctor` require the compact input, cache directory,
primary/verifier manifests and endpoints, and compute profile. Discovery also
supports offline replay, incremental baselines, confidence limits, bounded
retrieval/classification settings, and progress formatting.

The shared runtime flags are `--primary-model-manifest`,
`--verifier-model-manifest`, `--primary-base-url`, `--verifier-base-url`, and
`--allow-remote-inference`. Discovery selects progress rendering with
`--progress-format`; commands select final rendering with `--output-format`.

All graph commands accept `--output-format table|json|jsonl`. `doctor`,
`qualify`, runtime checks, release validation, and summaries accept table or JSON.
Progress is always written to stderr; result data is written to stdout.
Progress events cover stage boundaries, retries, bounded parse/classification
completion, cache and candidate reuse, quarantine totals, runtime/RSS, publication
storage, and terminal completion.

`prove` accepts `--from`, `--to`, `--max-hops`, and `--max-paths`. Paths are
ordered by fewest hops, highest bottleneck confidence, and stable node IDs.

`why-not` accepts a pair and relation. It distinguishes accepted, solver-rejected,
quarantined parse, model disagreement, assumption, invalid citation, NLI veto,
below threshold, inference failure, not retrieved, and unknown node outcomes.
