---
description: Inspect OddsFox Graph exports with DuckDB without running the inference pipeline.
---

# Analysts

Use this hub when you want to inspect OddsFox Graph output, not operate the
inference pipeline. OddsFox Graph ships software and local tooling, not a hosted
dataset.

<span class="of-persona of-persona--analyst">Analyst</span>

## Do you already have a build?

=== "Yes — open and query"

    Exported artifacts live under `build/` by default:

    ```text
    build/nodes.parquet
    build/edges.parquet
    build/odds_history.parquet
    build/stage_odds_history.parquet
    build/rejected_edges.parquet
    build/ontology.json
    build/inference_report.json
    ```

    Query with DuckDB:

    ```bash
    duckdb -c "SELECT type, count(*) FROM 'build/nodes.parquet' GROUP BY 1 ORDER BY 2 DESC"
    ```

    Continue with [Output artifacts](../reference/output-artifacts.md).

=== "No — need a run first"

    Ask an operator to complete [Quickstart](../getting-started/index.md) or
    [Running the pipeline](../guides/running-the-pipeline.md), then return here.
    Analysts do not need a live LLM for ordinary post-build queries.

Compiled market outcomes bridge into the exported graph via `REFERS_TO`
(and related logical edges) when the proposition compiler covers the market
template — see [Logical layer](../guides/logical-layer.md) for predicates and
rules. Residual / unrecognized markets may still lack that bridge — check
`proposition_json` on outcomes.

## Next pages

| Goal | Page |
| --- | --- |
| Propositions / rules | [Logical layer](../guides/logical-layer.md) |
| Column contracts | [Output artifacts](../reference/output-artifacts.md) |
| Allowed node/edge types | [Ontology](../reference/ontology.md) |
| Input parquet grain | [Source data schema](../reference/source-data-schema.md) |
| Common questions | [FAQ](../concepts/faq.md) |
