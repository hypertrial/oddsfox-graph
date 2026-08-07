---
description: Consume OddsFox Graph parquet exports safely—confidence, inference methods, ontology boundaries, and anti-patterns.
---

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

- Assuming every market has a compiled `Proposition` / `REFERS_TO` bridge
  (only deterministic templates are covered; residual types may still be
  disconnected)
- Treating residual LLM fragments as ground truth without confidence filtering
- Ignoring `rejected_edges.parquet` when debugging missing relationships
- Materializing transitive `IMPLIES` by hand instead of using `oddsgraph closure`

## Query recipes

Runnable DuckDB snippets over a completed `build/`. Adjust paths if you used
`--build-dir`.

Bridge covered outcomes to topology via `REFERS_TO`:

```bash
duckdb -c "
SELECT o.canonical_id AS outcome_id, o.label AS outcome,
       t.canonical_id AS entity_id, t.type AS entity_type, t.label AS entity
FROM 'build/edges.parquet' e
JOIN 'build/nodes.parquet' o ON o.canonical_id = e.source_id
JOIN 'build/nodes.parquet' t ON t.canonical_id = e.target_id
WHERE e.edge_type = 'REFERS_TO'
ORDER BY o.label
LIMIT 20
"
```

Filter edges by confidence (post-export equivalent of `--minimum-confidence`):

```bash
duckdb -c "
SELECT edge_type, count(*) AS n
FROM 'build/edges.parquet'
WHERE confidence >= 0.5
GROUP BY 1
ORDER BY 2 DESC
"
```

Debug missing relationships via rejection reasons:

```bash
duckdb -c "
SELECT rejection_reason, count(*) AS n
FROM 'build/rejected_edges.parquet'
GROUP BY 1
ORDER BY 2 DESC
"
```

Query transitive implications after `oddsgraph closure`:

```bash
duckdb -c "
SELECT source_id, target_id, premises
FROM 'build/implies_closure.parquet'
WHERE derivation_type = 'transitive'
LIMIT 20
"
```

## Next pages

| Goal | Page |
| --- | --- |
| Column schemas | [Output artifacts](../reference/output-artifacts.md) |
| Propositions / rules | [Logical layer](../guides/logical-layer.md) |
| Allowed patterns | [Ontology](../reference/ontology.md) |
| How IDs merge | [Entity resolution](../concepts/entity-resolution.md) |
| Input grain | [Source data schema](../reference/source-data-schema.md) |
| Caveats | [Known limitations](../concepts/limitations.md) |
| Compiler stages | [Architecture](../concepts/architecture.md) |
