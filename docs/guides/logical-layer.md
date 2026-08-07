---
description: Proposition compilation, structural logical edges, the WC2026 rule registry, build flags, and on-demand IMPLIES closure.
---

# Logical layer

OddsFox Graph attaches formal truth conditions to covered `OUTCOME` nodes,
emits structural logical edges at compile time, then optionally applies a
small deterministic rule registry. Transitive `IMPLIES` closure is on-demand.

## What compiles

The proposition compiler (`oddsgraph/propositions.py`) covers these market
categories:

| Category | Predicates | Typical Polymarket pattern |
| --- | --- | --- |
| Match moneyline / draw / advance | `wins_match`, `draws_match`, `advances_match` | `Team vs. Team`, draw markets, team-to-advance |
| Group winner | `wins_group` | `Group X Winner` |
| Stage of elimination | `eliminated_at_stage` | `Stage of Elimination: Team` |
| World Cup winner | `wins_competition` | tournament winner markets |
| Reaches stage | `reaches_stage` | `Nation to Reach Final/Semifinals/…`, knockout-advance |

Covered outcomes get a `Proposition` payload in `proposition_json`, for
example:

```json
{
  "predicate": "reaches_stage",
  "arguments": {
    "team": "team:canada",
    "competition": "competition:world-cup-2026",
    "stage": "stage:world-cup-2026:final"
  },
  "polarity": true
}
```

Residual / unrecognized market types still produce `EVENT` / `MARKET` /
`OUTCOME` structure **without** propositions — those outcomes stay
disconnected from topology. See [Known limitations](../concepts/limitations.md).

## Compile-time structural edges

Compiled outcomes get structural edges as follows (not every edge for every
outcome):

| Edge | When |
| --- | --- |
| `REFERS_TO` | Outcome → each topology entity named in proposition arguments |
| `PRICES` | Market → priced outcome / entity |
| `COMPLEMENT` | Binary YES/NO outcome pairs |
| `EXACTLY_ONE` | Constraint → member outcomes of an exclusive partition |

Match-result `EXACTLY_ONE` (win A / win B / draw) requires the **draw market**
in addition to the two team moneylines — without it the partition is incomplete
because a soccer match can still draw
(`oddsgraph/propositions.py:_build_match_propositions`). Separate
`EXACTLY_ONE` partitions still emit for team-to-advance markets (when both
advance outcomes are present) and for other exclusive templates such as group
winner / stage elimination / WC winner.

Proposition-compiler edges carry `derivation_type=compiler` on export;
residual LLM / template topology edges use `extraction`.

## Rule registry

When reasoning is enabled, `oddsgraph/rules.py` pairs propositions and emits
direct logical edges. Registered rules:

| `rule_id` | Edge type | Semantics |
| --- | --- | --- |
| `wc.stage_monotonicity` | `IMPLIES` | Reaching a later stage implies reaching every earlier marketed stage (unknown ranks skipped) |
| `wc.champion_reaches_final` | `IMPLIES` | Winning the competition implies reaching the Final |
| `wc.elimination_implies_reaches` | `IMPLIES` | Being eliminated at a knockout+ stage implies reaching that stage |
| `wc.champion_equals_elim_at_champion` | `EQUIVALENT` (bidirectional) | Winning the competition is equivalent to elimination-at-Champion |
| `wc.single_match_winner_mutex` | `MUTEX` (bidirectional) | Distinct team moneylines for the same match cannot both be true |

Rule-derived edges land in `edges.parquet` with:

- `derivation_type=rule`
- `rule_id` / `rule_version` identifying the registry entry
- `premises` holding the proposition keys that fired the rule

The registry is intentionally small and WC2026-specific — not LLM judgment.

## Build flags

| Flag | Default | Effect |
| --- | --- | --- |
| `--propositions` / `--no-propositions` | on | Compile propositions and structural logical edges |
| `--reasoning` / `--no-reasoning` | on | Apply the rule registry over compiled propositions |

Rules only ever run over compiled propositions (`pipeline.py` skips the rule
engine when no propositions were compiled). Disabling propositions therefore
leaves reasoning with nothing to pair. Disabling reasoning alone still keeps
`REFERS_TO` / `PRICES` / `COMPLEMENT` / `EXACTLY_ONE`.

## On-demand closure

`oddsgraph closure` reads exported `IMPLIES` edges and writes transitive
edges to `build/implies_closure.parquet`:

- Same columns as `edges.parquet`
- `derivation_type=transitive`
- `premises` holds the shortest path of node ids (`source` … `target`)

Closure is **not** materialized into `edges.parquet` by default. Prefer this
command over inventing transitive joins downstream — see
[Integrators](../audiences/integrators.md).

## See also

- [Architecture](../concepts/architecture.md)
- [Ontology](../reference/ontology.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Known limitations](../concepts/limitations.md)
- [Running the pipeline](running-the-pipeline.md)
