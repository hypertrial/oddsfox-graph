# oddsfox-graph Docs

This handbook keeps stable user guidance separate from dated benchmark notes
and low-level artifact schemas.

## Reading Order

1. Start with the [README](../README.md) for purpose, setup, and the shortest
   working build and inspect commands.
2. Use [CLI Reference](cli.md) when you need every command, flag, and skipped
   artifact error.
3. Use [Build Modes](builds.md) when choosing between full output,
   `--skip-prices`, `--skip-coherence`, and `--fast-graph`.
4. Use [Artifact Reference](artifacts.md) when consuming parquet files or
   markdown reports programmatically.
5. Use [Architecture](architecture.md) when changing the build pipeline,
   scoring logic, coherence solve, or evaluation flow.
6. Use [Benchmarks](benchmarks.md) when comparing performance or updating local
   timing notes.

## Maintenance Contract

The test suite includes lightweight docs drift checks in `tests/test_docs.py`.
Those checks verify that CLI subcommands, build flags, artifact names, report
names, manifest fields, and local Markdown links remain documented.
