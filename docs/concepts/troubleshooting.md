---
description: Fix common OddsFox Graph install, reduce, infer, build, explorer, and docs problems by stage.
---

# Troubleshooting

Common problems and fixes, grouped by stage.

## Install

### `uv sync` hangs or takes minutes compiling `llama-cpp-python`

PyPI only ships an sdist for `llama-cpp-python`, so a plain `uv sync` compiles
it from source (needs a C/C++ toolchain and CMake). On Apple Silicon, add the
Metal build flag instead:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --frozen --extra dev
```

On Linux or any CPU-only machine, install a prebuilt wheel instead of
compiling — see [Linux / CPU-only setup](../guides/linux-cpu-setup.md).

## `reduce` / input data

### DuckDB reports no files found for the input glob

`reduce` reads `data/*market_hourly_odds*.parquet` (or a repo-root fallback
matching `polymarket_wc2026_market_hourly_odds_*.parquet`) via DuckDB's
`read_parquet`. If nothing matches, DuckDB raises an IO error naming the glob.
Confirm the parquet and its `.schema.json` sidecar are placed at the
repository root or under `data/` — see
[Source data schema](../reference/source-data-schema.md). If you passed
`--data-dir`, note it is **exclusive**: there is no repo-root fallback once
set.

### `semantic_markets.parquet` has 0 rows

An empty reduce still writes a schema-typed parquet (zero rows, not a missing
file). Check that the input glob matched the expected hourly-odds export, that
the `.schema.json` sidecar is present beside it, and that any
`--event-id` / `--limit-events` filters did not exclude every market. Re-run
`oddsgraph -v reduce` and inspect the logged input path.

## `infer`

### Deterministic coverage is much lower than ~91%

Deterministic templates match on structured `event_title` patterns (see
[Deterministic topology](../guides/deterministic-topology.md)). Low coverage
on a dataset usually means event titles don't match the expected
`Team vs. Team`, `Group X Winner`, `Stage of Elimination`, or `Winner`
phrasing — check `inference_report.json`'s `per_event_status` for a sample of
events that fell through to the LLM path, and confirm team names resolve via
`oddsgraph/data/team_name_aliases.json`.

### `--resume` isn't reusing a fragment I expected it to

Resume reuses completed event fragments and matching chunk parts, but chunk
settings changes (token budgets, `max-markets-per-chunk`) invalidate stale
`__part*.json` files via a per-event chunk manifest — that's by design, not a
bug. For the deterministic-verification tier specifically, a verified
fragment (`__verified.json`) is only reused while its
`__verify_manifest.json` fingerprint still matches the current template
output. A failed verification deletes that event's verified artifacts so a
later `build` cannot load a stale `__verified.json`.

### Model not found at `models/qwen3-4b-q4_k_m.gguf`

Download it first — see [models/README.md](https://github.com/hypertrial/oddsfox-graph/blob/main/models/README.md)
or pass an explicit `--model-path`.

### Server backend requests fail / connection refused

`--llm-backend server` expects a running `llama-server` at `--server-url`
(default `http://127.0.0.1:8080`). Start it first — see
[llama-server](../guides/llama-server.md). `--concurrency` only has an effect
with this backend.

## `build` / explorer

### Explorer shows no data

`oddsgraph explore` requires `build/nodes.parquet` and `build/edges.parquet`
from a completed `oddsgraph build` (or `run`) first. Confirm `--build-dir`
matches between the build and explore invocations.

### Explorer time slider is missing or all matches stay uncolored

The Knockout time slider needs `build/odds_history.parquet` from
`oddsgraph odds-history` (also produced by `oddsgraph run`). Without that
artifact the bracket still loads from `nodes.parquet` / `edges.parquet`, but
MATCH cards have no `current_home_prob` coloring. Confirm `--build-dir`
matches and that the source parquet includes `soccer_team_to_advance`
markets.

## Docs

### `mkdocs build --strict` fails locally

Run `uv sync --extra docs` first, then `uv run mkdocs build --strict` from
the repo root. Strict mode fails on broken internal links and missing nav
entries — `tests/docs/test_docs_structure.py` enforces that every page under
`docs/` has a matching `mkdocs.yml` nav entry, so a new page needs both.

## See also

- [FAQ](faq.md)
- [Known limitations](limitations.md)
- [Configuration](../reference/configuration.md)
