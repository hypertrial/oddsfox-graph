---
description: Inspect OddsFox Graph exports with DuckDB or the local explorer without running the inference pipeline.
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
    build/rejected_edges.parquet
    build/ontology.json
    build/inference_report.json
    ```

    Query with DuckDB:

    ```bash
    duckdb -c "SELECT type, count(*) FROM 'build/nodes.parquet' GROUP BY 1 ORDER BY 2 DESC"
    ```

    Or launch the local explorer:

    ```bash
    uv sync --extra explore
    oddsgraph explore
    ```

    Continue with [Explorer](../guides/explorer.md) and
    [Output artifacts](../reference/output-artifacts.md).

=== "No — need a run first"

    Ask an operator to complete [Quickstart](../getting-started/index.md) or
    [Running the pipeline](../guides/running-the-pipeline.md), then return here.
    Analysts do not need a live LLM for ordinary post-build queries.

## Topology vs market layer

The default explorer view is a left-to-right knockout bracket (32 `MATCH`
cards, `ADVANCES_TO` edges, path highlight on click). Switch to **Full
topology** for `COMPETITION` / `STAGE` / `GROUP` / `ROUND` / `MATCH` / `TEAM`.
The market layer (`EVENT`, `MARKET`, `OUTCOME`) is currently **disconnected**:
the export has no `PRICES` or `IMPLIES` edges linking topology nodes to markets.
Search for an event title or market id to explore the market layer
independently.

## Next pages

| Goal | Page |
| --- | --- |
| Visual inspection | [Explorer](../guides/explorer.md) |
| Column contracts | [Output artifacts](../reference/output-artifacts.md) |
| Allowed node/edge types | [Ontology](../reference/ontology.md) |
| Input parquet grain | [Source data schema](../reference/source-data-schema.md) |
| Common questions | [FAQ](../concepts/faq.md) |
