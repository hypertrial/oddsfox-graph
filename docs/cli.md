# CLI Reference

Run commands with `python -m oddsfox_graph.cli <command>`.

## `build`

The DuckDB-only legacy builder accepts `--input`, `--out`, and optional
`--taxonomy`.

## `discover`

Self-hosted discovery accepts:

```text
--input --out --cache-dir --benchmark --incremental-from --compute-profile
--model-manifest --model-profile --require-ready --allow-unbenchmarked-rules
--offline --llm-base-url --llm-runtime --allow-remote-inference
--parse-model --classify-model --embedding-model --embedding-revision
--accept-confidence --relation-threshold --parse-confidence --top-k
--embedding-block-size --max-propositions --max-candidates --max-llm-pairs
--llm-concurrency --sampling-seed --temperature --generation-top-p
--generation-top-k --presence-penalty --parse-max-output-tokens
--classify-max-output-tokens
```

`--input` and `--out` are required. Online runs also require
`--model-manifest`. The default endpoint is
`http://127.0.0.1:8080/v1`, the runtime is `llama.cpp`, both production roles
default to `Qwen/Qwen3-4B-GGUF:Q8_0`, and concurrency defaults to 2.
`--llm-runtime` also accepts `vllm`. Non-loopback URLs require
`--allow-remote-inference`; credentials, query strings, and fragments are
always rejected.

The sampling defaults are seed 0, temperature 0.1, top-p 0.8, top-k 20,
presence penalty 1.5, 4,096 parse output tokens, and 1,024 classification
output tokens. A missing or mismatched profile routes model-positive proposals
to review. `--require-ready` requires a matching profile and test-partition
evaluation.

## `model-manifest`

Create a content-bound model declaration with:

```text
--model-path --model-id --revision --license --runtime --llm-base-url
--allow-remote-inference --output
```

The command hashes a GGUF file or model tree and preflights the loaded runtime.
The approved production license is `Apache-2.0`.

## `model-check`

Use `--model-manifest`, `--llm-base-url`, and optional
`--allow-remote-inference`. The command verifies health, loaded model identity,
runtime metadata, both production JSON schemas, token accounting, truncation,
and stable failure behavior.

## `model-profile`

Use `--input`, `--benchmark`, `--cache-dir`, `--model-manifest`, `--out`, and
optional `--allow-remote-inference`. Only calibration-partition rows are read.
Outputs are `model_profile.json`, `calibration_predictions.parquet`, and
`calibration_report.json`.

## `benchmark-export`

Use `--input`, `--out`, `--output-dir`, optional `--parse-count`,
`--pair-count`, and `--seed`. Defaults are 750 parses and 3,000 pairs.

## `benchmark-compile`

Use `--input`, `--review-a`, `--review-b`, `--adjudication`,
`--sampling-manifest`, and `--output`. The sampling manifest is required but
remains separate from blinded reviewer files.

## `evaluate`

Use `--out`, `--benchmark`, optional `--compute-profile`, and optional
`--output`. Historical `--pricing-file` remains accepted but is mutually
exclusive with compute accounting. Only the untouched test partition is
scored.

## `review-export`

Use `--out`, `--output`, optional `--accepted`, `--recall-pairs`, and `--seed`.

## `review-score`

Use `--out`, `--labels`, and optional `--output`.

## `benchmark-summary`

Use `--out` to summarize a completed manifest.

## `nodes`

Use `--out` and optional `--top`.

## `edges`

Use `--out`, optional `--edge-type`, and optional `--top`.

## `condition`

Use `--out`, `--a`, and `--b`.

## `explain`

Use `--out` and `--node`.

## `explain-edge`

Use `--out`, `--src`, `--dst`, and `--edge-type`.

## `search`

Use `--out`, `--query`, and optional `--top`.
