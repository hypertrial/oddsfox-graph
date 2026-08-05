# OddsFox Graph

Local, open-source pipeline that converts Polymarket WC2026 hourly-odds parquet
into an inferred logical graph of competitions, teams, stages, matches, markets,
outcomes, and relationships.

## Pipeline

```text
Polymarket parquet
    → semantic market records
    → local structured LLM extraction
    → entity resolution
    → graph validation
    → nodes.parquet + edges.parquet
```

**Performance note:** Local LLM inference (`infer`) dominates end-to-end wall-clock
time. Python stages (reduce, resolve, build) are comparatively fast. Tune chunk
settings below to reduce the number of LLM calls for large events.

## Source data

Place the Pipeline golden mart export at the repository root or under `data/`:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet`
- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

Grain: one row per `(market_id, odds_hour_epoch)` with primary-outcome hourly
OHLC and market/event metadata.

## Setup

```bash
uv sync --extra dev
```

On Apple Silicon, install `llama-cpp-python` with Metal support:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --extra dev
```

Download the local model (see `models/README.md`).

## CLI

```bash
oddsfox-graph reduce          # reduce parquet to semantic markets
oddsfox-graph infer           # infer graph fragments per event
oddsfox-graph build           # resolve, validate, export graph
oddsfox-graph validate        # validate exported artifacts
oddsfox-graph run             # full pipeline
```

Global options (all commands):

- `--build-dir <path>` — output directory for build artifacts
- `--data-dir <path>` — directory containing source parquet files
- `--verbose` / `-v` — enable INFO logging

Infer / run options:

- `--limit-events N`
- `--event-id <id>` (repeatable)
- `--model-path models/qwen3-4b-q4_k_m.gguf`
- `--resume / --no-resume`

Build / run options:

- `--minimum-confidence 0.5` — reject edges below this threshold during `build` (does not affect entity resolution)

### Chunking settings (infer)

Configured in `Settings` defaults in `oddsfox_graph/config.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `chunk_token_budget` | 6000 | Max estimated input tokens per LLM chunk |
| `chunk_output_token_budget` | 3000 | Max estimated output tokens per chunk |
| `max_markets_per_chunk` | 8 | Hard cap on markets per chunk |
| `max_text_field_chars` | 500 | Truncate long description fields in prompts |

## Output artifacts

```text
build/semantic_markets.parquet
build/fragments/<event_id>.json
build/nodes.parquet
build/edges.parquet
build/rejected_edges.parquet
build/inference_report.json
build/ontology.json
```

## Stack

- `duckdb` — query and reduce parquet
- `llama-cpp-python` — local Metal-accelerated inference
- `Qwen3-4B-Q4_K_M` — initial local model
- `pydantic` — constrained graph output schema
- `rapidfuzz` — entity and alias matching
- `rustworkx` — graph construction and validation
- `typer` — CLI
- `pytest` — tests

## Testing

```bash
uv run pytest
```

Live model integration tests (optional):

```bash
OF_LIVE_MODEL_TEST=1 uv run pytest -m integration
```
