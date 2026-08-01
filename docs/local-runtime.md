# Local stack on Apple Silicon

The wrapper keeps heavyweight state under gitignored `.oddsfox-runtime/` on the
SSD containing this checkout:

```bash
scripts/local_stack.sh fast
scripts/local_stack.sh serve-fast
scripts/local_stack.sh full
scripts/local_stack.sh serve-full
```

`fast` validates and builds the complete deterministic graph without starting or
checking either model service. `serve-fast` serves that completed output.

`full` requires previously installed weights, warm llama.cpp endpoints on ports
8080/8081, exact manifests, a compute profile, and a v0.12 automation profile.
It runs mode-aware doctor checks and upgrades the graph with bounded semantic
enrichment. Qualification remains a separate command and is outside the one-hour
discovery clock.

Setup, model download, manifests, runtime checks, and qualification helper
commands remain available for managing the external Qwen3-4B Q8 and Granite 3.3
2B runtimes. The wrapper binds Hugging Face, Torch, pip, npm, Playwright,
temporary, cache, log, model, and output directories below the SSD runtime root.
Set `ODDSFOX_RUNTIME_ROOT` only to another directory on `/Volumes/Mac SSD`; other
volumes require explicit `ODDSFOX_ALLOW_NON_SSD_RUNTIME=1`.
