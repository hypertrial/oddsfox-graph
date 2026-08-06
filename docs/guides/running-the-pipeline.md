# Running the pipeline

OddsFox Graph exposes a Typer CLI named `oddsgraph`. Every command shares
`--build-dir`, `--data-dir`, and `--verbose`.

## Commands

| Command | What it does |
| --- | --- |
| `oddsgraph reduce` | Reduce hourly odds parquet to semantic market records |
| `oddsgraph infer` | Infer graph fragments per event (deterministic + residual LLM) |
| `oddsgraph build` | Resolve entities, validate, export nodes/edges |
| `oddsgraph validate` | Validate exported artifacts |
| `oddsgraph explore` | Local Dash explorer over exported parquet |
| `oddsgraph run` | Full pipeline: reduce → infer → build → validate |

## Stage-by-stage

```bash
oddsgraph reduce
oddsgraph infer --limit-events 10 -v
oddsgraph build
oddsgraph validate
```

Or one shot:

```bash
oddsgraph run --limit-events 10 -v
```

`oddsgraph run` with `--event-id` / `--limit-events` reuses the same in-memory
market list for build, so the exported graph stays scoped to those events.
Standalone `oddsgraph build` still reads the full semantic parquet.

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
build/inference_report.json
build/ontology.json
```

## See also

- [Deterministic topology](deterministic-topology.md)
- [Official bracket](official-bracket.md)
- [llama-server](llama-server.md)
- [CLI](../reference/cli.md)
