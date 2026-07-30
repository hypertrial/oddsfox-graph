# Benchmarks

Use the manifest timing summary after a completed build:

```bash
python -m oddsfox_graph.cli benchmark-summary --out <build-dir>
```

The command prints:

- `runtime_seconds`
- selected count fields from `stats`
- artifact count
- top stage timings from `stage_timings`

There is no full-vs-fast compare mode in v0.2.0.
