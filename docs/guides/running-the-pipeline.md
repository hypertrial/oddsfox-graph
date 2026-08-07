---
description: Stage-by-stage oddsgraph CLI walkthrough for reduce, infer, build, validate, explore, closure, and run.
---

# Running the pipeline

OddsFox Graph exposes a Typer CLI named `oddsgraph`. Every command shares
`--build-dir`, `--data-dir`, and `--verbose`.

## Commands

| Command | What it does |
| --- | --- |
| `oddsgraph reduce` | Reduce hourly odds parquet to semantic market records |
| `oddsgraph infer` | Infer graph fragments per event (deterministic + residual LLM) |
| `oddsgraph build` | Resolve entities, compile propositions, apply rules, export |
| `oddsgraph validate` | Validate exported artifacts |
| `oddsgraph closure` | On-demand transitive `IMPLIES` closure |
| `oddsgraph explore` | Local Dash explorer over exported parquet |
| `oddsgraph run` | Full pipeline: reduce → infer → build → validate |

## Stage-by-stage

```bash
oddsgraph reduce
oddsgraph -v infer --limit-events 10
oddsgraph build
oddsgraph validate
oddsgraph closure   # optional
```

Or one shot:

```bash
oddsgraph -v run --limit-events 10
```

`oddsgraph run` with `--event-id` / `--limit-events` reuses the same in-memory
market list for build, and inferred/verified fragments are filtered to those
event IDs so the exported graph stays scoped. Standalone `oddsgraph build`
still reads the full semantic parquet (and all on-disk fragments) unless you
pass a narrowed market list programmatically.

## Useful flags

Infer / run:

```bash
oddsgraph infer \
  --llm-backend server \
  --concurrency 4 \
  --deterministic-topology \
  --resume
```

Build / run:

```bash
oddsgraph build --minimum-confidence 0.5 --official-bracket
oddsgraph build --no-propositions --no-reasoning
```

See [CLI](../reference/cli.md) for the complete flag list and
[Configuration](../reference/configuration.md) for `Settings` defaults.

## Output locations

Default build directory is `build/`:

```text
build/semantic_markets.parquet
build/fragments/
build/nodes.parquet
build/edges.parquet
build/rejected_edges.parquet
build/implies_closure.parquet   # from oddsgraph closure
build/inference_report.json
build/ontology.json
```

## See also

- [Deterministic topology](deterministic-topology.md)
- [Official bracket](official-bracket.md)
- [Inference backends](inference-backends.md)
- [llama-server](llama-server.md)
- [CLI](../reference/cli.md)
