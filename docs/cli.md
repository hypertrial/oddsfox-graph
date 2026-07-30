# CLI Reference

## `discover`

Required: `--input`, `--out`.

Runtime and provenance: `--cache-dir`, `--benchmark`, `--incremental-from`,
`--compute-profile`, `--llm-base-url`, `--llm-runtime`, `--model-manifest`,
`--model-profile`, `--allow-remote-inference`, `--require-ready`,
`--allow-unbenchmarked-rules`, `--offline`.

Models and acceptance: `--parse-model`, `--classify-model`,
`--embedding-model`, `--embedding-revision`, `--accept-confidence`,
`--relation-threshold`, `--parse-confidence`.

Limits: `--top-k`, `--embedding-block-size`, `--max-propositions`,
`--max-candidates`, `--max-llm-pairs`, `--llm-concurrency`.

Sampling: `--sampling-seed`, `--temperature`, `--generation-top-p`,
`--generation-top-k`, `--presence-penalty`, `--parse-max-output-tokens`,
`--classify-max-output-tokens`.

## Benchmark commands

- `benchmark-export`: `--input`, `--out`, `--output-dir`, `--parse-count`,
  `--pair-count`, `--seed`.
- `benchmark-compile`: `--input`, `--review-a`, `--review-b`,
  `--adjudication`, `--sampling-manifest`, `--output`.
- `evaluate`: `--out`, `--benchmark`, `--compute-profile`, `--output`.
- `benchmark-summary`: `--out`.

## Model commands

- `model-manifest`: `--model-path`, `--model-id`, `--revision`, `--license`,
  `--runtime`, `--llm-base-url`, `--allow-remote-inference`, `--output`.
- `model-check`: `--model-manifest`, `--llm-base-url`,
  `--allow-remote-inference`.
- `model-profile`: `--input`, `--benchmark`, `--cache-dir`,
  `--model-manifest`, `--out`, `--allow-remote-inference`.

## Query commands

- `nodes`: `--out`, `--top`.
- `edges`: `--out`, `--edge-type`, `--top`.
- `condition`: `--out`, `--a`, `--b`.
- `explain`: `--out`, `--node`.
- `explain-edge`: `--out`, `--src`, `--dst`, `--edge-type`.
- `search`: `--out`, `--query`, `--top`.
