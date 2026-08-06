---
description: Choose inprocess, llama-server, or MLX backends for residual LLM inference, including outlines and benchmarks.
---

# Inference backends

Residual events (those not covered by deterministic topology) go through
structured local LLM extraction. OddsFox Graph supports three backends for that
path. Runtime defaults live in `oddsgraph/config.py` and are mirrored in
[Configuration](../reference/configuration.md).

## Backend comparison

| Backend | When to use |
| --- | --- |
| `inprocess` (default) | Single-machine decode speed without a separate server; llama.cpp + outlines |
| `server` | Concurrent request pipelining across `llama-server` slots |
| `mlx` | Apple Silicon MLX checkpoints (`--extra mlx`) with outlines |

**Recommended local paths:** `--llm-backend inprocess` (default) or `mlx` for
single-machine decode speed. Use `--llm-backend server --concurrency N` when you
want concurrent request pipelining. The server path still uses GBNF over HTTP,
so expect it to be slower per token than inprocess/mlx.

```bash
oddsgraph infer --llm-backend inprocess
oddsgraph infer --llm-backend server --concurrency 4
oddsgraph infer --llm-backend mlx --mlx-model-path models/qwen3-4b-mlx
```

## Outlines constrained decoding

Raw llama.cpp JSON-schema GBNF grammars are slow on large-vocab models (Qwen3
~152k tokens): the sampler walks the vocab on CPU every decode step. oddsgraph
uses [`outlines`](https://github.com/dottxt-ai/outlines) FSM constrained
decoding for the `inprocess` and `mlx` backends, plus a compact wire schema
(`CompactGraphFragment` with short keys) to shrink required output tokens.
Qwen3 thinking mode is disabled via `/no_think` so decode budget goes to JSON.

Measured on Apple M4 (32GB) with `qwen3-4b-q4_k_m.gguf`, warm inprocess decode:

| Backend | Constraint | Approx tok/s |
| --- | --- | --- |
| `server` (prior) | llama.cpp GBNF | ~3.7–5.2 |
| `inprocess` (now) | outlines FSM + compact JSON | ~8.7–14.5 |

Benchmark locally:

```bash
uv run python scripts/benchmark_infer.py \
  --markets build/semantic_markets.parquet \
  --backends inprocess --limit 1 --n-ctx 4096,8192 \
  --event-id <residual-event-id>
```

Results write to `build/benchmark_report.json` (includes a Markdown table).

## MLX backend (Apple Silicon)

```bash
uv sync --frozen --extra mlx
# Convert an instruct checkpoint (example):
uv run python -m mlx_lm.convert \
  --hf-path Qwen/Qwen3-4B \
  --mlx-path models/qwen3-4b-mlx -q

oddsgraph infer --llm-backend mlx --mlx-model-path models/qwen3-4b-mlx
```

Live MLX integration test:

```bash
ODDSGRAPH_LIVE_MLX_TEST=1 uv run pytest -m integration -k mlx
```

## llama-server

For out-of-process pipelining, start `llama-server` and point oddsgraph at it.
Full startup flags and notes live in [llama-server](llama-server.md).

```bash
oddsgraph run --llm-backend server --concurrency 4
```

`--concurrency` only applies to the `server` backend.

## See also

- [llama-server](llama-server.md)
- [Deterministic topology](deterministic-topology.md)
- [Configuration](../reference/configuration.md)
- [Fine-tuning](finetuning.md)
- [Running the pipeline](running-the-pipeline.md)
