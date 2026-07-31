# OddsFox Graph

OddsFox Graph 0.9 builds a deterministic logical graph from compact Polymarket
market snapshots. It runs entirely against self-hosted open models: a Qwen3-4B
Q8 primary and Granite 3.3 2B verifier must independently agree before a
model-derived relation can be published. NLI can veto consensus but cannot
publish an edge.

`AUTOMATION_VALIDATED` means the exact model pair, runtimes, protocols, generated
cases, settings, and thresholds passed deterministic catalog-derived qualification.
It is not a claim of independently measured real-world semantic accuracy.

## Install

```bash
python -m pip install -e '.[dev]'
```

llama.cpp or vLLM, model weights, MiniLM embeddings, and ModernBERT NLI files are
managed outside this package. The package never downloads or launches a model.

## Input

The only accepted Parquet contract is `polymarket-market-snapshot-v1`:

- required: `market_id`, `question`, `outcomes`, `clob_token_ids`;
- optional: `event_id`, `event_slug`, `description`, `start_time`, `end_time`,
  `category`, and `tags`;
- `outcomes` and `clob_token_ids` must be nonempty equal-length `VARCHAR[]`
  columns and token IDs must be globally unique.

## Local model workflow

Run Qwen on `127.0.0.1:8080` and Granite on `127.0.0.1:8081`, then create and
check one content-bound manifest per runtime:

```bash
oddsfox-graph model-manifest --model-path /models/qwen.gguf \
  --model-id Qwen/Qwen3-4B-GGUF:Q8_0 --revision <revision> \
  --license Apache-2.0 --runtime llama.cpp \
  --llm-base-url http://127.0.0.1:8080/v1 --output primary.json

oddsfox-graph model-check --model-manifest primary.json \
  --llm-base-url http://127.0.0.1:8080/v1
```

Repeat for Granite and port 8081. `doctor` checks the catalog, both runtimes,
licenses, cache, local dependencies, compute profile, and output capacity.

## Qualify and discover

```bash
oddsfox-graph qualify --input data/polymarket.parquet --out qualification \
  --cache-dir cache --primary-model-manifest primary.json \
  --verifier-model-manifest verifier.json --compute-profile compute.json

oddsfox-graph discover --input data/polymarket.parquet --out output/graph \
  --cache-dir cache --primary-model-manifest primary.json \
  --verifier-model-manifest verifier.json --compute-profile compute.json \
  --progress-format plain
```

Online discovery runs or reuses exact automated qualification. A cache-complete
replay uses `--offline`; an incremental build also supplies
`--incremental-from <completed-output>`. Older caches and outputs require clean
regeneration.

## Query

Every query supports table, JSON, and JSONL output:

```bash
oddsfox-graph search --out output/graph --query bitcoin --output-format json
oddsfox-graph prove --out output/graph --from <node> --to <node> --max-hops 4
oddsfox-graph why-not --out output/graph --a <node> --b <node> \
  --relation implies --output-format json
```

Python callers use `oddsfox_graph.graph.Graph` for typed immutable nodes, edges,
proofs, proof steps, and diagnostics.

See [architecture](docs/architecture.md), [discovery](docs/discovery.md),
[qualification](docs/qualification.md), [artifacts](docs/artifacts.md),
[CLI](docs/cli.md), and [release validation](docs/release.md).
