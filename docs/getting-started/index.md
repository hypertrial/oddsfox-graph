---
description: Install OddsFox Graph, place a Polymarket WC2026 export, download a local model, and run the pipeline once.
---

# Quickstart

Install OddsFox Graph, place a Polymarket WC2026 hourly-odds export, download a
local model, and run the pipeline once.

## 1. Install

```bash
uv sync --frozen --extra dev
```

On Apple Silicon with Metal:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --frozen --extra dev
```

## 2. Place source data

Copy a Pipeline golden mart export to the repository root or `data/`:

```text
polymarket_wc2026_market_hourly_odds_<timestamp>.parquet
polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json
```

Grain: one row per `(market_id, odds_hour_epoch)`. See
[Source data schema](../reference/source-data-schema.md).

## 3. Download the model

```bash
huggingface-cli download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf \
  --local-dir models \
  --local-dir-use-symlinks False
mv models/Qwen3-4B-Q4_K_M.gguf models/qwen3-4b-q4_k_m.gguf
```

Details live in `models/README.md`.

## 4. Run

Smoke on a few events:

```bash
oddsgraph -v run --limit-events 5
```

Full pipeline:

```bash
oddsgraph run
```

## 5. Inspect outputs

```text
build/semantic_markets.parquet
build/fragments/<event_id>.json
build/nodes.parquet
build/edges.parquet
build/odds_history.parquet
build/stage_odds_history.parquet
build/rejected_edges.parquet
build/inference_report.json
build/ontology.json
```

## Next steps

- [Running the pipeline](../guides/running-the-pipeline.md)
- [Deterministic topology](../guides/deterministic-topology.md)
- [llama-server](../guides/llama-server.md)
- [Output artifacts](../reference/output-artifacts.md)
