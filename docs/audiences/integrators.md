# Integrators

Use this hub when you consume OddsFox Graph exports in another system.

<span class="of-persona of-persona--integrator">Integrator</span>

## Consumption contract

Treat exported parquet as **inferred local logical knowledge graph artifacts**, not as:

- live market data
- execution signals
- an authoritative tournament bracket without checking `inference_method`

Primary outputs:

| Artifact | Purpose |
| --- | --- |
| `nodes.parquet` | Canonical nodes with type, label, aliases, confidence, evidence |
| `edges.parquet` | Canonical edges with type, confidence, evidence, inference method |
| `rejected_edges.parquet` | Edges dropped by validation (with `rejection_reason`) |
| `ontology.json` | Allowed node/edge types and patterns |
| `inference_report.json` | Per-event status and aggregate counts |

See [Output artifacts](../reference/output-artifacts.md) for column contracts.

## Confidence and evidence

- Prefer rows with higher `confidence` and non-empty `evidence_market_ids`.
- Inspect `inference_method` (`deterministic`, `official_bracket`, LLM path, etc.).
- Inspect `resolution_method` on nodes for how labels merged into canonical IDs.

## Ontology boundary

Only use edge patterns listed in [Ontology](../reference/ontology.md). Do not
invent new node or edge types in downstream code without updating the ontology
and validation path.

## Anti-patterns

- Joining topology nodes to markets as if `PRICES` / `IMPLIES` edges already exist
- Treating residual LLM fragments as ground truth without confidence filtering
- Ignoring `rejected_edges.parquet` when debugging missing relationships

## Next pages

| Goal | Page |
| --- | --- |
| Column schemas | [Output artifacts](../reference/output-artifacts.md) |
| Allowed patterns | [Ontology](../reference/ontology.md) |
| How IDs merge | [Entity resolution](../concepts/entity-resolution.md) |
| Input grain | [Source data schema](../reference/source-data-schema.md) |

## See also

- [Output artifacts](../reference/output-artifacts.md)
- [Ontology](../reference/ontology.md)
- [Known limitations](../concepts/limitations.md)
- [Architecture](../concepts/architecture.md)
