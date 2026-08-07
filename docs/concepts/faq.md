---
description: Answers about hosted data, inference speed, Metal, topology-market bridging, and configuration defaults.
---

# FAQ

## Does OddsFox Graph host data?

No. This repository is Hypertrial-owned MIT software. Documentation does not
host datasets. Operators supply their own Pipeline golden mart export and local
model weights.

## Why is infer so slow?

LLM inference dominates wall-clock time. Deterministic topology skips the LLM
for template-covered events (~91% on WC2026). For residual events, prefer
`--llm-backend inprocess` (default) or `mlx` for single-machine decode speed;
use `--llm-backend server --concurrency N` when you want concurrent request
pipelining. See [Inference backends](../guides/inference-backends.md).

## Do I need Metal?

Metal-accelerated `llama-cpp-python` is recommended on Apple Silicon. CI and
CPU-only environments can install the prebuilt CPU wheel instead. See
[Linux / CPU-only setup](../guides/linux-cpu-setup.md).

## Are topology and markets connected?

Yes for markets covered by the proposition compiler. Those `OUTCOME` nodes
carry a formal `Proposition` and link into topology via `REFERS_TO` (plus
`PRICES` / `COMPLEMENT` / `EXACTLY_ONE`). Deterministic rules then add
`IMPLIES` / `EQUIVALENT` / `MUTEX`. Residual / unrecognized market types may
still lack propositions — see [Known limitations](limitations.md).

## Where do configuration defaults live?

In `oddsgraph/config.py`. The [Configuration](../reference/configuration.md)
page mirrors those code defaults.

## See also

- [Architecture](architecture.md)
- [Glossary](glossary.md)
- [Known limitations](limitations.md)
- [Troubleshooting](troubleshooting.md)
- [Inference backends](../guides/inference-backends.md)
- [Explorer](../guides/explorer.md)
- [Quickstart](../getting-started/index.md)
