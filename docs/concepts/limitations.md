# Known limitations

A single place for caveats that are otherwise scattered across guides and
audience pages. Read this before treating any exported artifact as more
authoritative than it is.

## Topology and market layers are disconnected

The exported graph has no `PRICES` or `IMPLIES` edges linking `MATCH` /
`TEAM` topology nodes to `EVENT` / `MARKET` / `OUTCOME` market nodes.
Expanding from a topology node in the explorer will not reach markets, and
joining the two layers downstream requires your own matching logic (for
example on `event_title` or market id). See
[Output artifacts](../reference/output-artifacts.md) and
[Ontology](../reference/ontology.md) for the exact edge patterns that do
exist today.

## Scope is WC2026 / Polymarket only

Deterministic templates, team alias/code tables, and the official bracket
fragment (`oddsgraph/data/wc2026_schedule.json`) are all built against the
Polymarket WC2026 hourly-odds schema and FIFA's 2026 World Cup structure.
Nothing here generalizes to other tournaments or other prediction-market
platforms without new templates, alias tables, and a new schedule export.

## Residual LLM output is lower-confidence than deterministic/official paths

Events not covered by deterministic templates or the official bracket go
through chunked local LLM extraction. This path is inherently less reliable
than template-driven extraction. Always check `inference_method` and
`confidence` before trusting an edge or node, and prefer filtering with
`--minimum-confidence` for downstream use. See
[Entity resolution](entity-resolution.md) and
[Integrators](../audiences/integrators.md).

## Fine-tuning scripts are experimental

`scripts/export_finetune_dataset.py`, `scripts/finetune_lora.py`, and
`scripts/eval_finetuned_model.py` are not part of the stable CLI surface.
Flags and output paths may change without the same compatibility
expectations as `oddsgraph`'s core commands. See
[Fine-tuning](../guides/finetuning.md).

## No hosted service or bundled data

OddsFox Graph is Hypertrial-owned MIT **software**, not a hosted dataset or
API. You must supply your own Pipeline golden mart export and local model
weights; nothing here is fetched or served on your behalf.

## Apple Silicon is the primary tested environment

Backend performance guidance (`inprocess`, `mlx`, `llama-server` tuning) is
based on Apple Silicon + Metal measurements. CPU-only / Linux paths work
(see [Linux / CPU-only setup](../guides/linux-cpu-setup.md)) but are
exercised mainly through CI correctness checks, not performance
benchmarking.

## See also

- [FAQ](faq.md)
- [Troubleshooting](troubleshooting.md)
- [Architecture](architecture.md)
