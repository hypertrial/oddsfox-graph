---
description: Install OddsFox Graph, place source data, choose an LLM backend, and run the pipeline end to end.
---

# Operators

Use this hub when you install OddsFox Graph, keep a local model available, and
run the inference pipeline.

<span class="of-persona of-persona--operator">Operator</span>

## Install

```bash
uv sync --frozen --extra dev
```

On Apple Silicon, install `llama-cpp-python` with Metal support:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --frozen --extra dev
```

On Linux or any CPU-only machine, see
[Linux / CPU-only setup](../guides/linux-cpu-setup.md) to avoid compiling
`llama-cpp-python` from source.

Download the recommended GGUF model (see `models/README.md`):

```bash
huggingface-cli download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf \
  --local-dir models \
  --local-dir-use-symlinks False
mv models/Qwen3-4B-Q4_K_M.gguf models/qwen3-4b-q4_k_m.gguf
```

## Place source data

Put a Pipeline golden mart export at the repository root or under `data/`:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet`
- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

## Run end to end

```bash
oddsgraph run
```

Prefer the server backend when you want concurrent residual inference on a
large full-dataset run:

```bash
llama-server -m models/qwen3-4b-q4_k_m.gguf -ngl -1 -c 12288 -np 4 -cb -fa on \
  --host 127.0.0.1 --port 8080
oddsgraph run --llm-backend server --concurrency 4
```

## Backend choices

Prefer `inprocess` (default) or `mlx` for single-machine decode; use `server`
for concurrent pipelining. Full comparison, outlines notes, and MLX setup:
[Inference backends](../guides/inference-backends.md).

## Next pages

| Goal | Page |
| --- | --- |
| First successful run | [Quickstart](../getting-started/index.md) |
| Stage-by-stage CLI | [Running the pipeline](../guides/running-the-pipeline.md) |
| Backend choice / MLX | [Inference backends](../guides/inference-backends.md) |
| Faster infer (server) | [llama-server](../guides/llama-server.md) |
| Settings defaults | [Configuration](../reference/configuration.md) |
| Deterministic coverage | [Deterministic topology](../guides/deterministic-topology.md) |
| Official bracket | [Official bracket](../guides/official-bracket.md) |
| Common failures | [Troubleshooting](../concepts/troubleshooting.md) |
| Common questions | [FAQ](../concepts/faq.md) |
