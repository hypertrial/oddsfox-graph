# OddsFox Graph

OddsFox Graph 0.11 builds and visualizes logical connections across every valid
proposition in a compact Polymarket catalog. Discovery has two explicit modes:

- `fast` is model-free, complete in node coverage, and publishes only exact
  source-contract or strict deterministic-rule edges. It is the release-gated
  path and targets a ready explorer in under two minutes on an Apple M4 Air.
- `full` upgrades that deterministic core with local embeddings, USearch ANN,
  NLI vetoes, and Qwen/Granite consensus. It is functional but carries the
  `EXPERIMENTAL_FULL` validation status until separate live certification.

Fast mode is deliberately conservative: complete proposition coverage does not
mean exhaustive relationship recall. Full mode also evaluates a bounded
candidate set, not all 17.97 billion possible proposition pairs.

## Install

```bash
python -m pip install -e '.[dev]'
```

Install deterministic MP4 recording separately so ordinary graph queries and
CLI help do not import a browser runtime:

```bash
python -m pip install -e '.[recording]'
python -m playwright install chromium
```

llama.cpp or vLLM and all model weights remain external. The local wrapper keeps
models, caches, outputs, logs, and temporary files below `.oddsfox-runtime/` on
the SSD containing this checkout.

## Input

The Parquet contract is `polymarket-market-snapshot-v1`:

- required: `market_id`, `question`, `outcomes`, `clob_token_ids`;
- optional: `event_id`, `event_slug`, `description`, `start_time`, `end_time`,
  `category`, and `tags`;
- outcome/token arrays must be nonempty and equal length, and token IDs must be
  globally unique.

## Fast graph

```bash
oddsfox-graph doctor --mode fast --input data/polymarket.parquet --out output/fast
oddsfox-graph discover --mode fast --input data/polymarket.parquet \
  --out output/fast --deadline-seconds 120 --progress-format plain
oddsfox-graph serve --out output/fast --open-browser
oddsfox-graph record --out output/fast --destination recordings/fast-story
```

Omitting `--max-propositions` selects the complete valid catalog. A development
run may set it explicitly. Fast mode neither starts nor imports inference,
embedding, NLI, or cache code.

## Full enrichment

Create and qualify both local model manifests out of band, then run:

```bash
oddsfox-graph discover --mode full --input data/polymarket.parquet \
  --out output/full --cache-dir cache \
  --automation-profile qualification/automation_profile.json \
  --primary-model-manifest primary.json --verifier-model-manifest verifier.json \
  --compute-profile compute.json --deadline-seconds 3600
```

The profile must exactly bind the v0.11 extractor, ANN, NLI, prompts, schemas,
settings, manifests, and runtime pair. A deadline cutoff stops new model work,
finishes in-flight requests, and publishes the valid deterministic core plus any
accepted enrichment; the command exits nonzero when the requested SLA is missed.

The explorer filters `source_contract`, `deterministic_rule`, and
`generative_consensus` evidence. Python callers use
`oddsfox_graph.graph.Graph` for typed queries, proofs, and why-not diagnostics.

See [architecture](docs/architecture.md), [discovery](docs/discovery.md),
[explorer](docs/explorer.md), [qualification](docs/qualification.md),
[artifacts](docs/artifacts.md), [CLI](docs/cli.md),
[recording](docs/recording.md), [performance](docs/performance.md), and
[release validation](docs/release.md).
