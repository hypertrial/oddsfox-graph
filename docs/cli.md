# CLI Reference

Entry point:

```bash
python -m oddsfox_graph.cli <command> ...
```

## `build`

Build structural graph artifacts from an input parquet file.

| Flag | Required | Description |
|---|---|---|
| `--input` | yes | Source odds parquet path |
| `--out` | yes | Output directory |
| `--taxonomy` | no | Optional taxonomy JSON; defaults to bundled WC2026 taxonomy |

## `discover`

Discover logical relations from a compact market snapshot or OddsFox
minutely/hourly export.

| Flag | Required | Description |
|---|---|---|
| `--input` | yes | Source parquet path |
| `--out` | yes | Atomically published output directory |
| `--cache-dir` | no | Content-addressed request cache; defaults beside `--out` |
| `--benchmark` | no | Reviewed v0.4 benchmark; enables rule gates and evaluation |
| `--incremental-from` | no | Distinct, manifest-complete discovery baseline |
| `--pricing-file` | no | Versioned per-model token pricing JSON |
| `--require-ready` | no | Exit nonzero unless evaluation returns `READY_TO_SCALE` |
| `--offline` | no | Require a terminal cache entry for every task, replay cached failures, and never use an API key |
| `--parse-model` | no | Structured parser model (default `gpt-5.6-terra`) |
| `--classify-model` | no | Pair classifier model (default `gpt-5.6-terra`) |
| `--embedding-model` | no | Local sentence-transformer model |
| `--embedding-revision` | no | Pinned model revision |
| `--accept-confidence` | no | Minimum accepted classifier confidence (default 0.95) |
| `--relation-threshold` | no | Repeatable `RELATION=VALUE` acceptance override |
| `--parse-confidence` | no | Minimum confidence for parse-backed rules (default 0.95) |
| `--top-k` | no | Semantic neighbors retained per proposition (default 20) |
| `--embedding-block-size` | no | Exact cosine block size (default 512) |
| `--max-propositions` | no | Maximum selected propositions (default 5,000) |
| `--max-candidates` | no | Maximum canonical candidates (default 400,000) |
| `--max-llm-pairs` | no | Maximum classified unresolved pairs (default 5,000) |
| `--llm-concurrency` | no | Maximum concurrent Responses calls (default 8) |

Default relation thresholds are 0.995 for complement, 0.99 for equivalence and
mutual exclusion, and 0.98 for implication and compatibility.

## `benchmark-export`

Create deterministic, blinded reviewer templates and a sampling manifest.
Required flags are `--input`, `--out`, and `--output-dir`; optional flags are
`--parse-count`, `--pair-count`, and `--seed`.

## `benchmark-compile`

Compile two independent completed reviews plus adjudication into typed,
source-hash-bound `benchmark.parquet`. Required flags are `--input`,
`--review-a`, `--review-b`, `--adjudication`, and `--output`.

## `evaluate`

Evaluate a build against a compiled benchmark and write
`evaluation_report.json`. Required flags are `--out` and `--benchmark`;
`--pricing-file` and `--output` are optional. The command exits nonzero unless
the decision is `READY_TO_SCALE`.

## `review-export`

Export a deterministic, stratified CSV of accepted edges and candidate-recall
near misses.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed discovery directory |
| `--output` | yes | Review CSV destination |
| `--accepted` | no | Accepted-edge sample size (default 200) |
| `--recall-pairs` | no | Candidate and noncandidate audit pairs (default 200) |
| `--seed` | no | Deterministic sampling seed (default 0) |

## `review-score`

Score completed labels, write `evaluation.json`, and exit nonzero if any
quality gate fails.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed discovery directory |
| `--labels` | yes | Completed review CSV |
| `--output` | no | Evaluation JSON destination |

## `benchmark-summary`

Print runtime and count summary from `build_manifest.json`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |

## `nodes`

List nodes from `nodes.parquet`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--top` | no | Max rows (default 50) |

## `edges`

List accepted logic edges from `logic_edges.parquet`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--edge-type` | no | One of `compatible`, `complement`, `equivalent`, `implies`, `mutually_exclusive` |
| `--top` | no | Max rows (default 50) |

## `condition`

Show exact conditional rows for a resolved node pair.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--a` | yes | Source node id or unique text |
| `--b` | yes | Destination node id or unique text |

## `explain`

Explain one node: identity, same-market siblings, logic edges, conditionals.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--node` | yes | Node id or unique text |

## `explain-edge`

Explain one typed logic edge and related conditionals.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--src` | yes | Source node id or unique text |
| `--dst` | yes | Destination node id or unique text |
| `--edge-type` | yes | One of `compatible`, `complement`, `equivalent`, `implies`, `mutually_exclusive` |

## `search`

Search nodes by id, question, proposition, or outcome label.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--query` | yes | Search text |
| `--top` | no | Max rows (default 20) |
