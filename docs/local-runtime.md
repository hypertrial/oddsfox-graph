# Local stack on Apple Silicon

The wrapper keeps heavyweight state under gitignored `.oddsfox-runtime/` on the
SSD containing this checkout:

```bash
scripts/local_stack.sh fast "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet"
scripts/local_stack.sh serve-fast
scripts/local_stack.sh full "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet"
scripts/local_stack.sh serve-full
```

The input can instead be supplied with `ODDSFOX_WC2026_INPUT`. Product
commands always pass `--input-profile polymarket-wc2026-graph-hourly-v1` and
fail before starting model services when the export is missing. The generic
catalog is isolated under `generic-benchmark-smoke`; its output is not a
supported human explorer.

`fast` validates and builds the complete deterministic graph without starting or
checking either model service. `serve-fast` serves that completed WC2026 output.

`full` requires previously installed weights, warm llama.cpp endpoints on ports
8080/8081, exact manifests, a compute profile, and a v0.13 automation profile.
It runs mode-aware doctor checks and upgrades the graph with bounded semantic
enrichment. Qualification remains a separate command and is outside the one-hour
discovery clock.

Setup, model download, manifests, runtime checks, and qualification helper
commands remain available for managing the external Qwen3-4B Q8 and Granite 3.3
2B runtimes. The wrapper binds Hugging Face, Torch, pip, npm, Playwright,
temporary, cache, log, model, and output directories below the SSD runtime root.
Set `ODDSFOX_RUNTIME_ROOT` only to another directory on `/Volumes/Mac SSD`; other
volumes require explicit `ODDSFOX_ALLOW_NON_SSD_RUNTIME=1`.
