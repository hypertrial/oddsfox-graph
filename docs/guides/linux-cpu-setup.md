# Linux / CPU-only setup

Every other install snippet in these docs assumes Apple Silicon with Metal.
This page covers running oddsgraph on Linux or any machine without a GPU
backend for `llama-cpp-python`.

## The problem

`llama-cpp-python` only ships an sdist on PyPI. A plain
`uv sync --frozen --extra dev` therefore compiles it from source on first
install, which needs a working C/C++ toolchain and CMake and can take several
minutes.

## The fix: install the prebuilt CPU wheel

CI uses this exact approach (see `.github/workflows/ci.yml`) to avoid
compiling `llama-cpp-python` on every run. Do the same locally:

```bash
uv sync --frozen --extra dev --no-install-package llama-cpp-python

# Resolve the exact pinned version from uv.lock
LLAMA_VER=$(uv export --frozen --no-emit-project --no-annotate --no-hashes \
  | sed -n 's/^llama-cpp-python==\([^; ]*\).*/\1/p' | head -n 1)

# Install the prebuilt CPU wheel for that version instead of compiling
uv pip install "llama-cpp-python==${LLAMA_VER}" \
  --index https://abetlen.github.io/llama-cpp-python/whl/cpu
```

This installs the same locked version as `uv.lock`, just from a prebuilt CPU
wheel index instead of compiling the sdist.

## Running on CPU

The `inprocess` backend (default) works on CPU, just without Metal
acceleration — expect noticeably slower decode than on Apple Silicon. The
`mlx` backend is Apple Silicon only and is not an option here.

To minimize LLM work on CPU:

- Keep `--deterministic-topology` enabled (default) — it skips the LLM
  entirely for ~91% of WC2026 events. See
  [Deterministic topology](deterministic-topology.md).
- Leave `--verify-deterministic` off (default) unless you specifically need
  the confirm/patch pass, since it adds an LLM call per deterministic event.
- Start with `--limit-events N` to size the residual workload before running
  the full dataset.

`llama-server` (see [llama-server](llama-server.md)) also builds/runs on
Linux CPU if you prefer an out-of-process backend, but it will not be faster
than `inprocess` without GPU offload.

## See also

- [Running the pipeline](running-the-pipeline.md)
- [Deterministic topology](deterministic-topology.md)
- [Troubleshooting](../concepts/troubleshooting.md)
- [FAQ](../concepts/faq.md)
