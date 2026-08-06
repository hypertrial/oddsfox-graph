# FAQ

## Does OddsFox Graph host data?

No. This repository is Hypertrial-owned MIT software. Documentation does not
host datasets. Operators supply their own Pipeline golden mart export and local
model weights.

## Why is infer so slow?

LLM inference dominates wall-clock time. Deterministic topology skips the LLM
for template-covered events (~91% on WC2026). For residual events, prefer
`--llm-backend server --concurrency N` on Apple Silicon.

## Do I need Metal?

Metal-accelerated `llama-cpp-python` is recommended on Apple Silicon. CI and
CPU-only environments can install the prebuilt CPU wheel instead. See
[Linux / CPU-only setup](../guides/linux-cpu-setup.md).

## Are topology and markets connected?

Not yet in the exported graph. There are currently no `PRICES` / `IMPLIES` edges
linking `MATCH` / `TEAM` nodes to `EVENT` / `MARKET` / `OUTCOME` nodes. Use
search in the explorer to inspect the market layer separately.

## Where do configuration defaults live?

In `oddsgraph/config.py`. The [Configuration](../reference/configuration.md)
page mirrors those code defaults.

## See also

- [Architecture](architecture.md)
- [Known limitations](limitations.md)
- [Troubleshooting](troubleshooting.md)
- [Explorer](../guides/explorer.md)
- [Quickstart](../getting-started/index.md)
