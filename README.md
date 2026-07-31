# OddsFox Graph

OddsFox Graph discovers explicit logical relationships between Polymarket
propositions. It uses deterministic rules, local embeddings, an optional local
NLI cascade, schema-constrained open-model inference, and an RC2 consistency
solver. Model-derived edges publish only through a matching human-benchmark
profile.

## Install

Python 3.11 or newer is required:

```bash
python -m pip install -e .
```

llama.cpp and vLLM are external inference runtimes. The repository does not
download or start model weights.

## Input

`discover` accepts Parquet using the
`polymarket-market-snapshot-v1` contract. Required columns are `market_id`,
`question`, `outcomes`, and `clob_token_ids`; the two list columns must be
nonempty and equal length for a row to be eligible. Rows with missing or
mismatched required values are counted and excluded. Supported metadata
columns are `event_id`, `event_slug`, `description`, `volume`, `start_time`,
`end_time`, `category`, and `tags`. Each token ID must be unique.

The release catalog is
`data/polymarket_all_markets_20260730T093857Z.parquet`, SHA-256
`790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2`.
The `data/` directory is intentionally unversioned; provide this external
fixture for real-data, performance, incremental, and release validation.

## Discover

Create and check a model manifest before an online run:

```bash
oddsfox-graph model-manifest \
  --model-path /models/qwen3-4b-q8.gguf \
  --model-id Qwen/Qwen3-4B-GGUF:Q8_0 \
  --revision <upstream-revision> \
  --license Apache-2.0 \
  --runtime llama.cpp \
  --llm-base-url http://127.0.0.1:8080/v1 \
  --output model-manifest.json

oddsfox-graph model-check \
  --model-manifest model-manifest.json \
  --llm-base-url http://127.0.0.1:8080/v1

oddsfox-graph discover \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery \
  --cache-dir output/cache \
  --model-manifest model-manifest.json
```

Without a model profile, model-positive proposals go to review. Use
`benchmark-export`, independent reviews, `benchmark-compile`, and
`model-profile` before a release run. A cache-complete replay uses `--offline`;
incremental execution uses a distinct completed directory with
`--incremental-from`.

The cache directory contains one transactional
`inference-cache-v6.sqlite3` database. v0.7 JSON caches, profiles, and
incremental baselines are intentionally incompatible. Candidate reasons are
used only for retrieval and scheduling; classification receives the two
canonical proposition records and never treats retrieval metadata as evidence.

See [documentation](docs/index.md), [CLI reference](docs/cli.md),
[discovery workflow](docs/discovery.md), [artifacts](docs/artifacts.md), and
[benchmark gates](docs/benchmarks.md).
