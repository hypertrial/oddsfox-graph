# oddsgraph

**Build prediction market logical knowledge graphs.**

oddsgraph is a local, open-source **Logical Knowledge Graph Compiler** for
prediction markets. It converts Polymarket WC2026 hourly-odds parquet into a
logical knowledge graph of competitions, teams, stages, matches, markets,
outcomes, and relationships.

## Pipeline

```text
Polymarket parquet
    → semantic market records
    → deterministic topology (match/group/stage templates)
    → official WC2026 bracket (curated FIFA schedule)
    → local structured LLM extraction (residual events only)
    → entity resolution
    → graph validation
    → nodes.parquet + edges.parquet
```

**Performance note:** Local LLM inference (`infer`) dominates end-to-end wall-clock
time. By default, oddsgraph extracts TEAM/MATCH/GROUP/STAGE topology
deterministically from structured Polymarket fields for the vast majority of
events (~91% on WC2026 data), and only sends unrecognized/ambiguous events
through the LLM. See
[Deterministic topology](docs/guides/deterministic-topology.md) and
[Inference backends](docs/guides/inference-backends.md).

## Source data

Place the Pipeline golden mart export at the repository root or under `data/`:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet`
- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

Grain: one row per `(market_id, odds_hour_epoch)` with primary-outcome hourly
OHLC and market/event metadata. Full contract:
[Source data schema](docs/reference/source-data-schema.md).

## Setup

```bash
uv sync --frozen --extra dev
```

`uv.lock` is committed for reproducible installs. CI uses the same lockfile and
installs a prebuilt CPU wheel for `llama-cpp-python` (PyPI only ships an sdist).

On Apple Silicon, install `llama-cpp-python` with Metal support:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --frozen --extra dev
```

On Linux / CPU-only machines, see
[Linux / CPU-only setup](docs/guides/linux-cpu-setup.md).

Download the local model (see `models/README.md`).

## Documentation

Public docs: [https://graph.oddsfox.io/](https://graph.oddsfox.io/)

Local preview:

```bash
uv sync --extra docs
uv run mkdocs serve -a 127.0.0.1:8000
```

Start from [docs/getting-started/index.md](docs/getting-started/index.md).

## CLI

```bash
oddsgraph reduce          # reduce parquet to semantic markets
oddsgraph infer           # infer graph fragments per event
oddsgraph build           # resolve, validate, export graph
oddsgraph validate        # validate exported artifacts
oddsgraph odds-history    # knockout advance odds time series for explorer
oddsgraph explore         # local Dash explorer over nodes/edges parquet
oddsgraph run             # full pipeline
```

Full flag reference: [CLI](docs/reference/cli.md). Stage walkthrough:
[Running the pipeline](docs/guides/running-the-pipeline.md). Settings defaults:
[Configuration](docs/reference/configuration.md).

Key topics covered in the docs site:

- [Deterministic topology](docs/guides/deterministic-topology.md) — template
  extraction (~91% of WC2026 events) and optional `--verify-deterministic`
- [Official bracket](docs/guides/official-bracket.md) — curated FIFA schedule
  injection on `build`
- [Logical layer](docs/guides/logical-layer.md) — propositions, structural
  logical edges, WC2026 rules, and on-demand `IMPLIES` closure
- [Inference backends](docs/guides/inference-backends.md) — `inprocess` /
  `server` / `mlx`, outlines constrained decoding, benchmarks
- [llama-server](docs/guides/llama-server.md) — concurrent residual inference
- [Fine-tuning](docs/guides/finetuning.md) — experimental LoRA scripts
- [Glossary](docs/concepts/glossary.md) — few-shot exemplars, resume, confidence,
  and related terms

## Output artifacts

```text
build/semantic_markets.parquet
build/fragments/<event_id>.json
build/nodes.parquet
build/edges.parquet
build/odds_history.parquet
build/rejected_edges.parquet
build/inference_report.json
build/ontology.json
```

Column contracts: [Output artifacts](docs/reference/output-artifacts.md).

## Explore

```bash
uv sync --extra explore   # or: uv sync --extra dev --extra explore
oddsgraph odds-history    # optional temporal odds for the explorer slider
oddsgraph explore         # http://127.0.0.1:8050
```

Default view is a left-to-right knockout bracket with an hourly win-probability
slider (when `odds_history.parquet` is present). Compiled propositions still
bridge covered markets into the exported graph via `REFERS_TO` — residual types
may still lack that bridge; see [Explorer](docs/guides/explorer.md) and
[Known limitations](docs/concepts/limitations.md).

## Stack

- `duckdb` — query and reduce parquet
- `httpx` — optional `llama-server` HTTP client
- `llama-cpp-python` — local Metal-accelerated inference
- `outlines` — FSM constrained decoding for structured JSON
- `mlx-lm` — optional Apple Silicon backend (`--extra mlx`)
- `Qwen3-4B-Q4_K_M` — initial local GGUF model
- `pydantic` — constrained graph output schema
- `rapidfuzz` — entity, alias, and few-shot exemplar matching
- `rustworkx` — graph construction and validation
- `typer` — CLI
- `dash` / `dash-cytoscape` — optional local graph explorer (`--extra explore`)
- `pytest` — tests

## Testing

```bash
uv run pytest
```

Live model integration tests (optional):

```bash
ODDSGRAPH_LIVE_MODEL_TEST=1 uv run pytest -m integration
```

Live server integration tests (optional, requires running `llama-server`):

```bash
ODDSGRAPH_LIVE_SERVER_TEST=1 uv run pytest -m integration
```

Live MLX integration tests (optional, Apple Silicon + converted MLX model):

```bash
ODDSGRAPH_LIVE_MLX_TEST=1 uv run pytest -m integration -k mlx
```
