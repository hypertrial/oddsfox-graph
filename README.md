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
oddsgraph explore         # local Dash explorer over nodes/edges parquet
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
- `--mlx-model-path models/qwen3-4b-mlx` — MLX model directory when `--llm-backend mlx`
- `--resume / --no-resume` — reuse completed event fragments and matching chunk
  parts (default: on). Changing chunk settings clears stale `__part*.json` files
  via a per-event chunk manifest
- `--llm-backend inprocess|server|mlx` — in-process llama.cpp+outlines (default),
  remote `llama-server`, or Apple Silicon `mlx-lm`+outlines
- `--server-url http://127.0.0.1:8080` — base URL when using `--llm-backend server`
- `--concurrency N` — concurrent LLM requests (server backend only)
- `--deterministic-topology / --no-deterministic-topology` — extract TEAM/MATCH/GROUP/STAGE topology without LLM when possible (default: on)
- `--verify-deterministic / --no-verify-deterministic` — opt-in LLM confirm/patch
  pass over deterministic topology (default: **off**)
- `--few-shot / --no-few-shot` — include rapidfuzz-ranked curated exemplars in
  residual LLM prompts (default: on)

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

### LLM verification tier (opt-in, default off)

When enabled, every deterministic-covered event gets a cheap LLM confirm/patch
pass instead of trusting the template mapping alone:

```bash
oddsgraph infer --verify-deterministic
```

Statuses in `inference_report.json`:

- `deterministic_verified` — LLM returned the same topology
- `deterministic_corrected` — LLM returned a patched fragment (saved as
  `build/fragments/<event_id>__verified.json`)

### Few-shot exemplars (default on)

Residual LLM prompts include up to `few_shot_top_k` curated examples from
`oddsgraph/data/llm_exemplars.json`, ranked with `rapidfuzz` against the current
event title/question. Disable with `--no-few-shot`. When enabled, infer reserves
~700 tokens × `few_shot_top_k` from `chunk_token_budget` so exemplar blocks do
not blow the model context window.

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

`oddsgraph run` with `--event-id` / `--limit-events` reuses the same in-memory
market list for build, so the exported graph stays scoped to those events
(standalone `oddsgraph build` still reads the full semantic parquet).

### Chunking settings (infer)

Configured in `Settings` defaults in `oddsgraph/config.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `n_ctx` | 8192 | Model context window (input + output per chunk) |
| `chunk_token_budget` | 5000 | Max estimated input tokens per LLM chunk |
| `chunk_output_token_budget` | 4096 | Max estimated output tokens per chunk |
| `max_markets_per_chunk` | 24 | Hard cap on markets per chunk |
| `max_text_field_chars` | 500 | Truncate long description fields in prompts |
| `flash_attn` | true | Enable Metal flash attention (in-process backend) |
| `n_batch` / `n_ubatch` | 1024 | llama.cpp batch sizes (in-process backend) |
| `llm_concurrency` | 2 | Concurrent requests when `--llm-backend server` |
| `deterministic_topology` | true | Skip full extraction for template-covered events |
| `verify_deterministic` | false | Opt-in LLM confirm/patch over deterministic events |
| `use_few_shot_exemplars` | true | Inject curated few-shot examples into residual prompts |
| `few_shot_top_k` | 2 | How many exemplars to include |
| `official_bracket` | true | Inject curated FIFA schedule bracket on build |
| `competition_label` | World Cup 2026 | Label/slug for COMPETITION nodes |

### Faster inference: outlines-constrained decoding

Raw llama.cpp JSON-schema GBNF grammars are slow on large-vocab models (Qwen3
~152k tokens): the sampler walks the vocab on CPU every decode step. oddsgraph
now uses [`outlines`](https://github.com/dottxt-ai/outlines) FSM constrained
decoding for the `inprocess` and `mlx` backends, plus a compact wire schema
(`CompactGraphFragment` with short keys) to shrink required output tokens.
Qwen3 thinking mode is disabled via `/no_think` so decode budget goes to JSON.

Measured on Apple M4 (32GB) with `qwen3-4b-q4_k_m.gguf`, warm inprocess decode:

| Backend | Constraint | Approx tok/s |
|---------|------------|--------------|
| `server` (prior) | llama.cpp GBNF | ~3.7–5.2 |
| `inprocess` (now) | outlines FSM + compact JSON | ~8.7–14.5 |

**Recommended local paths:** `--llm-backend inprocess` (default) or `mlx`.
The `server` backend still uses GBNF over HTTP and is mainly for out-of-process
pipelining — expect it to be slower per token.

Benchmark locally:

```bash
uv run python scripts/benchmark_infer.py \
  --markets build/semantic_markets.parquet \
  --backends inprocess --limit 1 --n-ctx 4096,8192 \
  --event-id <residual-event-id>
```

Results write to `build/benchmark_report.json` (includes a Markdown table).

### MLX backend (Apple Silicon)

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

### Faster infer with llama-server (optional)

For out-of-process pipelining on Apple Silicon, start `llama-server`:

```bash
llama-server -m models/qwen3-4b-q4_k_m.gguf -ngl -1 -c 8192 -np 2 -cb -fa on \
  --host 127.0.0.1 --port 8080
```

Then:

```bash
oddsgraph run --llm-backend server --concurrency 2
```

Install `llama-server` via Homebrew: `brew install llama.cpp`. Prefer
`inprocess`/`mlx` for single-machine speed.

### Fine-tuning / self-distillation (MLX LoRA)

Use deterministic/verified fragments as teacher labels:

```bash
# 1) Export chat JSONL from fragments + markets
uv run python scripts/export_finetune_dataset.py \
  --markets build/semantic_markets.parquet \
  --fragments-dir build/fragments \
  --output-dir build/finetune

# 2) Train LoRA adapter (Apple Silicon + --extra mlx)
uv run python scripts/finetune_lora.py \
  --model models/qwen3-4b-mlx \
  --data build/finetune \
  --iters 200

# 3) Evaluate node/edge F1 on the held-out split
uv run python scripts/eval_finetuned_model.py \
  --model models/qwen3-4b-mlx \
  --adapter-path build/finetune/adapters \
  --valid build/finetune/valid.jsonl
```

Point `--mlx-model-path` at a fused/adapted model once eval F1 looks good.

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

## Explore

Launch a local, read-only Dash + Cytoscape explorer over exported graph artifacts:

```bash
uv sync --extra explore   # or: uv sync --extra dev --extra explore
oddsgraph explore         # http://127.0.0.1:8050
```

Options: `--host`, `--port`, `--debug`, plus the shared `--build-dir`.

**Default view** is a left-to-right knockout bracket: 32 `MATCH` cards connected
by `ADVANCES_TO` edges (Round of 32 → … → Final / Third Place), laid out with a
deterministic preset + taxi edges. Click a match to highlight its path. Switch
**View → Full topology** for the broader
`COMPETITION`/`STAGE`/`GROUP`/`ROUND`/`MATCH`/`TEAM` graph (~180 nodes). Search
or expand from the bracket opens Full topology so the 32-node canvas stays
clean. Use Advanced filters for type/confidence/layout. Click any node or edge
to inspect exported features (confidence, aliases, evidence market IDs,
inference/resolution methods, evidence text).

**Important:** the topology layer and the market layer are currently
**disconnected**. The export has no `PRICES` or `IMPLIES` edges linking
`MATCH`/`TEAM` nodes to `EVENT`/`MARKET`/`OUTCOME` nodes, so expanding a
topology node will not reach markets. Search for an event title (or market id)
to explore the market layer independently.

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
