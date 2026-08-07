---
description: Definitions for OddsFox Graph terms including fragments, residual events, confidence, and inference methods.
---

# Glossary

Short definitions for OddsFox Graph terminology. Each term links to its
authoritative page when one exists.

## Logical Knowledge Graph

A typed graph of competitions, teams, stages, matches, markets, outcomes, and
relationships inferred from prediction-market records. Exported as
`nodes.parquet` and `edges.parquet`. See
[Output artifacts](../reference/output-artifacts.md).

## Logical Knowledge Graph Compiler

What OddsFox Graph *is*: a local compiler that turns Polymarket WC2026
hourly-odds parquet into a validated logical knowledge graph. Stages map to
lexer / parser / semantic analysis / linker / type check / codegen phases. See
[Architecture](architecture.md).

## Deterministic topology

Template-driven extraction of TEAM / MATCH / GROUP / STAGE structure from
structured Polymarket fields, without an LLM call. Covers roughly 91% of
WC2026 events. See [Deterministic topology](../guides/deterministic-topology.md).

## Residual event

An event that deterministic templates do not cover. Residual events go through
chunked local LLM extraction during `infer`.

## Fragment

A per-event JSON graph piece under `build/fragments/<event_id>.json`, produced
by deterministic topology or residual LLM extraction before entity resolution.
See [Output artifacts](../reference/output-artifacts.md).

## Chunk / chunk manifest

A residual-LLM batch of markets sized to token budgets. A per-event chunk
manifest records chunk settings so `--resume` can invalidate stale
`__part*.json` files when budgets change. See
[Configuration](../reference/configuration.md).

## Resume

Default `infer` behavior that reuses completed event fragments and matching
chunk parts. Changing chunk settings or market membership clears stale part
files (and completed event fragments) via the chunk manifest. See
[CLI](../reference/cli.md).

## Few-shot exemplar

A curated residual-LLM example from `oddsgraph/data/llm_exemplars.json`, ranked
with RapidFuzz against the current event title/question and injected into the
prompt when `--few-shot` is enabled (default on).

## Official bracket

Curated FIFA WC2026 schedule fragment injected during `build` from
`oddsgraph/data/wc2026_schedule.json` (stage ladder, 104 matches, knockout
`ADVANCES_TO` edges). See [Official bracket](../guides/official-bracket.md).

## Verification tier

Optional LLM confirm/patch pass over deterministic topology
(`--verify-deterministic`). Statuses in `inference_report.json`:

- `deterministic_verified` — LLM returned the same topology
- `deterministic_corrected` — LLM returned a patched fragment

See [Deterministic topology](../guides/deterministic-topology.md).

## Entity resolution / resolution tier

Merge of fragment-local nodes into canonical IDs via ordered tiers (exact
Polymarket ids, slug/label, alias, fuzzy RapidFuzz). Counts appear in
`inference_report.json` under `resolution_tiers`. See
[Entity resolution](entity-resolution.md).

## Confidence

Float in `[0, 1]` on nodes and edges. `--minimum-confidence` rejects edges
below the threshold during `build` without changing entity resolution.

## Canonical node / edge

Resolved, export-ready graph elements with stable ids, type, evidence, and
provenance fields. Schemas: `CanonicalNode` / `CanonicalEdge`. See
[Output artifacts](../reference/output-artifacts.md).

## Proposition

Formal truth condition attached to a covered `OUTCOME` node (`predicate`,
`arguments`, `polarity`, …). Compiled deterministically from market templates
during `build`. See [Ontology](../reference/ontology.md).

## Rule engine / derivation type

Deterministic logical rules over propositions that emit direct `IMPLIES`,
`EQUIVALENT`, or `MUTEX` edges. Edge provenance uses `derivation_type`
(`extraction`, `compiler`, `rule`, `transitive`). Transitive `IMPLIES`
closure is on-demand via `oddsgraph closure`.

## Ontology / allowed edge pattern

Allowed node types, edge types, and `(source → target)` patterns in
`oddsgraph/ontology.py`, dumped to `build/ontology.json`. Invalid patterns are
rejected. See [Ontology](../reference/ontology.md).

## Inference method

Provenance string on nodes/edges describing how the element was produced
(examples: `deterministic`, `official_bracket`, residual LLM path). Prefer
filtering on this field when consuming exports. See
[Integrators](../audiences/integrators.md).

## Rejected edge

An edge dropped during build/validation (ontology violation, confidence floor,
or evidence checks), written to `rejected_edges.parquet` with a
`rejection_reason`. See [Output artifacts](../reference/output-artifacts.md).

## See also

- [Architecture](architecture.md)
- [Known limitations](limitations.md)
- [FAQ](faq.md)
