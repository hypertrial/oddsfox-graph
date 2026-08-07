---
description: Complete oddsgraph CLI reference for reduce, infer, build, validate, explore, closure, and run flags.
---

# CLI

Entry point: `oddsgraph` (Typer). Shared options apply to every command.

## Global options

| Option | Description |
| --- | --- |
| `--build-dir <path>` | Directory for build artifacts |
| `--data-dir <path>` | Directory containing source parquet (exclusive when set; no repo-root fallback) |
| `--verbose` / `-v` | Enable INFO logging |

## `oddsgraph reduce`

Reduce hourly odds parquet to semantic market records.

No command-specific flags.

## `oddsgraph infer`

Infer graph fragments per event using deterministic topology and/or a local LLM.

| Option | Description |
| --- | --- |
| `--model-path` | Path to GGUF model file |
| `--mlx-model-path` | Path to MLX model directory |
| `--limit-events N` | Limit number of events to infer (`N >= 0`) |
| `--event-id <id>` | Specific event ID (repeatable) |
| `--resume` / `--no-resume` | Skip residual fragments and existing `__verified.json` artifacts (default: on) |
| `--llm-backend` | `inprocess`, `server`, or `mlx` (invalid values error) |
| `--server-url` | Base URL when `--llm-backend server` |
| `--concurrency N` | Concurrent LLM requests (server backend only) |
| `--deterministic-topology` / `--no-deterministic-topology` | Extract TEAM/MATCH/GROUP/STAGE without LLM when possible |
| `--verify-deterministic` / `--no-verify-deterministic` | LLM confirm/patch pass over deterministic topology (default: off) |
| `--few-shot` / `--no-few-shot` | Include rapidfuzz-ranked few-shot exemplars in residual prompts |
| `--chunk-token-budget` | Approx input-token budget per residual chunk |
| `--chunk-output-token-budget` | Approx output-token budget per residual chunk |
| `--max-markets-per-chunk` | Hard cap on markets included in one residual chunk |

## `oddsgraph build`

Resolve entities, compile propositions, apply logical rules, and export artifacts.

| Option | Description |
| --- | --- |
| `--minimum-confidence` | Minimum edge confidence threshold |
| `--official-bracket` / `--no-official-bracket` | Inject curated WC2026 stage ladder and official MATCH bracket |
| `--propositions` / `--no-propositions` | Compile formal propositions onto OUTCOME nodes (default: on) |
| `--reasoning` / `--no-reasoning` | Apply deterministic logical rules over propositions (default: on) |

## `oddsgraph validate`

Validate exported graph artifacts. Exits non-zero on failure.

## `oddsgraph closure`

Compute on-demand transitive `IMPLIES` closure from exported edges into
`build/implies_closure.parquet`. Requires a prior `oddsgraph build`.

## `oddsgraph explore`

Launch a local, read-only graph explorer.

| Option | Description |
| --- | --- |
| `--host` | Bind host (default `127.0.0.1`) |
| `--port` | Port (default `8050`) |
| `--debug` | Enable Dash debug / hot-reload |

Requires `--extra explore`.

## `oddsgraph run`

Full pipeline: reduce → infer → build → validate. Accepts the infer flags and
build flags listed above.

## See also

- [Running the pipeline](../guides/running-the-pipeline.md)
- [Configuration](configuration.md)
