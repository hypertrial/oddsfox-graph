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

Options:

- `--limit-events N`
- `--event-id <id>` (repeatable)
- `--model-path models/qwen3-4b-q4_k_m.gguf`
- `--resume / --no-resume`
- `--minimum-confidence 0.5`

## Output artifacts

```text
build/semantic_markets.parquet
build/fragments/<event_id>.json
build/nodes.parquet
build/edges.parquet
build/rejected_edges.parquet
build/unresolved_entities.parquet
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
