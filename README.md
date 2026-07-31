# OddsFox Graph

OddsFox Graph 0.10 discovers a deterministic logical graph across compact
Polymarket market snapshots and ships a local, interactive explorer for browsing
the resulting propositions, events, connected components, proofs, and diagnostics.

Inference is self-hosted. Qwen3-4B Q8 and Granite 3.3 2B must independently agree
before a model-derived relation can publish; NLI may veto consensus but cannot
publish an edge. `AUTOMATION_VALIDATED` certifies deterministic generated-case
conformance, reproducibility, and performance for the exact runtime pair. It is
not an independently measured claim about real-world semantic accuracy.

## Install

```bash
python -m pip install -e '.[dev]'
```

llama.cpp or vLLM, model weights, MiniLM embeddings, and ModernBERT NLI files are
external. The package never downloads or launches a model.

## Input

The accepted Parquet contract is `polymarket-market-snapshot-v1`:

- required: `market_id`, `question`, `outcomes`, `clob_token_ids`;
- optional: `event_id`, `event_slug`, `description`, `start_time`, `end_time`,
  `category`, and `tags`;
- outcomes and token IDs must be nonempty, equal-length arrays, and token IDs
  must be globally unique.

## Discover the complete catalog

After creating and checking one Apache-2.0 model manifest per local runtime:

```bash
oddsfox-graph discover --input data/polymarket.parquet --out output/graph \
  --cache-dir cache --primary-model-manifest primary.json \
  --verifier-model-manifest verifier.json --compute-profile compute.json \
  --all-propositions --progress-format plain
```

The default remains a bounded 5,000-proposition development run. Use
`--all-propositions` for the complete catalog. The classification budget is
balanced across event scopes, and coverage is published explicitly rather than
implying that every retrieved pair was classified. Cache-complete replay uses
`--offline`; incremental runs add `--incremental-from <completed-output>`.
When a run must meet a minimum pair-assessment envelope, set
`--classification-coverage-target` or `--max-visible-coverage-gap` together with
a sufficient `--max-llm-pairs` budget.

## Explore

```bash
oddsfox-graph serve --out output/graph --open-browser
```

The loopback-only server opens an interactive WebGL graph. Start at event or
component level, search for a proposition, expand a bounded neighborhood, filter
relations and confidence, inspect provenance, prove implications, or explain why
a requested edge is absent.

Create a portable, bounded snapshot for an event, component, or neighborhood:

```bash
oddsfox-graph explorer-export --out output/graph --destination export/event \
  --scope event --identifier <event-key>
```

Static exports query bundled Parquet with DuckDB-Wasm and never contain the full
catalog unless the declared scope fits the explicit response ceilings.

Python callers use `oddsfox_graph.graph.Graph` for typed nodes, edges, pages,
views, proofs, and diagnostics.

See [architecture](docs/architecture.md), [discovery](docs/discovery.md),
[explorer](docs/explorer.md), [qualification](docs/qualification.md),
[artifacts](docs/artifacts.md), [CLI](docs/cli.md), and
[release validation](docs/release.md).
