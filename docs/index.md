---
description: Build prediction market logical knowledge graphs with OddsFox Graph, a local Logical Knowledge Graph Compiler for Polymarket WC2026.
hide:
  - navigation
  - toc
---

<div class="of-hero" markdown>

<div class="of-hero__copy" markdown>

<span class="of-eyebrow">Build prediction market logical knowledge graphs</span>

# OddsFox Graph

OddsFox Graph is a local **Logical Knowledge Graph Compiler** for prediction
markets. It converts Polymarket WC2026 hourly-odds parquet into a logical
knowledge graph of competitions, teams, stages, matches, markets, outcomes, and
relationships.

Hypertrial-owned MIT software. No hosted service or bundled production data.
[FAQ](concepts/faq.md) ·
[License](https://github.com/hypertrial/oddsfox-graph/blob/main/LICENSE).

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[Explore the graph](guides/explorer.md){ .md-button }

</div>

<div class="of-hero__mark">
  <img src="assets/images/oddsfox-white.png" alt="">
  <span>Graph</span>
</div>

</div>

<div class="of-install" markdown>

**Start in the repository**

```bash
uv sync --frozen --extra dev
```

</div>

## Start with a task

<div class="of-task-grid" markdown>

<article class="of-task-card" markdown>

### Analyze the graph

Open exported `nodes.parquet` / `edges.parquet`, or launch the local Dash
explorer over a completed build.

[Analysts hub](audiences/analysts.md)

</article>

<article class="of-task-card" markdown>

### Operate the pipeline

Install the project, download a local model, and run `oddsgraph run` end to end.

[Operators hub](audiences/operators.md)

</article>

<article class="of-task-card" markdown>

### Contribute code

Change inference, resolution, ontology, explorer, or docs with the right tests.

[Contributors hub](audiences/contributors.md)

</article>

<article class="of-task-card" markdown>

### Integrate downstream

Consume documented graph artifacts without treating inference output as
execution or live market data.

[Integrators hub](audiences/integrators.md)

</article>

</div>

## Pipeline

```text
Polymarket parquet
    → semantic market records
    → deterministic topology (match/group/stage templates)
    → official WC2026 bracket (curated FIFA schedule)
    → local structured LLM extraction (residual events only)
    → entity resolution
    → graph validation
    → nodes.parquet + edges.parquet
```

See [Architecture](concepts/architecture.md) for stage details and
[Running the pipeline](guides/running-the-pipeline.md) for CLI walkthroughs.

This site is software documentation and does not host datasets.
