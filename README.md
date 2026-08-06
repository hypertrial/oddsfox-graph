# oddsgraph

Local, open-source pipeline that converts Polymarket WC2026 hourly-odds parquet
into an inferred logical graph of competitions, teams, stages, matches, markets,
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
through the LLM. Disable with `--no-deterministic-topology` if needed. Python
stages (reduce, resolve, build) are comparatively fast. Tune chunk settings
below to further reduce LLM calls for residual large events.

## Source data

Place the Pipeline golden mart export at the repository root or under `data/`:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet`
- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

Grain: one row per `(market_id, odds_hour_epoch)` with primary-outcome hourly
OHLC and market/event metadata.

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

Download the local model (see `models/README.md`).

## CLI

```bash
oddsgraph reduce          # reduce parquet to semantic markets
oddsgraph infer           # infer graph fragments per event
oddsgraph build           # resolve, validate, export graph
oddsgraph validate        # validate exported artifacts
oddsgraph run             # full pipeline
```

Global options (all commands):

- `--build-dir <path>` — output directory for build artifacts
- `--data-dir <path>` — directory containing source parquet files (exclusive when set; no repo-root fallback)
- `--verbose` / `-v` — enable INFO logging

Infer / run options:

- `--limit-events N`
- `--event-id <id>` (repeatable)
- `--model-path models/qwen3-4b-q4_k_m.gguf`
- `--resume / --no-resume` — reuse completed event fragments and matching chunk
  parts (default: on). Changing chunk settings clears stale `__part*.json` files
  via a per-event chunk manifest
- `--llm-backend inprocess|server` — in-process `llama-cpp-python` or remote `llama-server`
- `--server-url http://127.0.0.1:8080` — base URL when using `--llm-backend server`
- `--concurrency N` — concurrent LLM requests (server backend only)
- `--deterministic-topology / --no-deterministic-topology` — extract TEAM/MATCH/GROUP/STAGE topology without LLM when possible (default: on)

### Deterministic topology (default on)

`infer` / `run` classify each event from structured fields before any LLM call:

| Template | Example `event_title` | Extracted topology |
|----------|----------------------|--------------------|
| Match | `Brazil vs. Morocco - Exact Score` | TEAM ×2, MATCH, PARTICIPATES_IN |
| Group winner | `World Cup Group D Winner` | TEAM, GROUP, PARTICIPATES_IN |
| Stage of elimination | `World Cup: Portugal Stage of Elimination` | TEAM, STAGE, QUALIFIES_FOR |
| Tournament winner | `World Cup Winner` | TEAM, STAGE(Champion), QUALIFIES_FOR |

Team names are canonicalized via `oddsgraph/data/team_name_aliases.json` (e.g.
`Korea Republic` → `South Korea`, `IR Iran` → `Iran`) so group membership and
match nodes merge cleanly. FIFA/Polymarket team codes live in
`oddsgraph/data/team_codes.json` — note Polymarket WC2026 slugs use `kor` for
**Curaçao** and `kr` for **South Korea**.

Covered events are recorded as `deterministic` in `inference_report.json` and
skip LLM chunking entirely. On the WC2026 dataset this covers ~91% of events and
cuts estimated LLM chunk volume by ~15×. Player-prop markets (`soccer_player_*`)
still get MARKET/OUTCOME nodes, but add no extra topology beyond the match
pairing. Unrecognized events (e.g. Golden Ball, fun props) continue through the
existing chunked LLM path.

Escape hatch:

```bash
oddsgraph infer --no-deterministic-topology
```

### Official WC2026 bracket (default on)

`build` / `run` inject a curated fragment from `oddsgraph/data/wc2026_schedule.json`
(104 FIFA-reviewed fixtures exported once from `oddsfox-pipeline`'s OpenFootball
warehouse). This adds:

- Stage ladder: `Group Stage → Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final → Champion`, plus `Semifinals → Third Place` (`STAGE ADVANCES_TO STAGE`)
- All 104 matches placed with `MATCH PART_OF STAGE`
- Knockout progression `MATCH ADVANCES_TO MATCH` derived from team continuity across consecutive stages (~32 edges)
- `inference_method=official_bracket` on those edges/nodes

Regenerate the schedule snapshot (requires local `oddsfox-pipeline` DuckDB):

```bash
uv run python scripts/export_wc2026_schedule.py
```

Escape hatch:

```bash
oddsgraph build --no-official-bracket
```

Build / run options:

- `--minimum-confidence 0.5` — reject edges below this threshold during `build` (does not affect entity resolution)
- `--official-bracket / --no-official-bracket` — inject curated WC2026 stage ladder + official MATCH bracket (default: on)

### Chunking settings (infer)

Configured in `Settings` defaults in `oddsgraph/config.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `n_ctx` | 12288 | Model context window (input + output per chunk) |
| `chunk_token_budget` | 7000 | Max estimated input tokens per LLM chunk |
| `chunk_output_token_budget` | 4096 | Max estimated output tokens per chunk |
| `max_markets_per_chunk` | 24 | Hard cap on markets per chunk |
| `max_text_field_chars` | 500 | Truncate long description fields in prompts |
| `flash_attn` | true | Enable Metal flash attention (in-process backend) |
| `n_batch` / `n_ubatch` | 1024 | llama.cpp batch sizes (in-process backend) |
| `llm_concurrency` | 4 | Concurrent requests when `--llm-backend server` |
| `deterministic_topology` | true | Skip LLM for template-covered events |
| `official_bracket` | true | Inject curated FIFA schedule bracket on build |
| `competition_label` | World Cup 2026 | Label/slug for COMPETITION nodes |

### Faster infer with llama-server (optional)

For large full-dataset runs on Apple Silicon, start `llama-server` with continuous
batching and point oddsgraph at it:

```bash
llama-server -m models/qwen3-4b-q4_k_m.gguf -ngl -1 -c 12288 -np 4 -cb -fa on \
  --host 127.0.0.1 --port 8080
```

Then run infer (or the full pipeline) against the server:

```bash
oddsgraph run --llm-backend server --concurrency 4
```

Install `llama-server` via Homebrew: `brew install llama.cpp`. The default
in-process backend (`--llm-backend inprocess`) remains the default and does not
require a separate server process.

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
- `httpx` — optional `llama-server` HTTP client
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
ODDSGRAPH_LIVE_MODEL_TEST=1 uv run pytest -m integration
```

Live server integration tests (optional, requires running `llama-server`):

```bash
ODDSGRAPH_LIVE_SERVER_TEST=1 uv run pytest -m integration
```
