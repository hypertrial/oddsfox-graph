---
description: Allowed node types, edge types, and source-target patterns in the OddsFox Graph ontology.
---

# Ontology

Canonical node types, edge types, and allowed `(source, target)` patterns live
in `oddsgraph/ontology.py` and are exported to `build/ontology.json`.

## Node types

| Type | Role |
| --- | --- |
| `COMPETITION` | Tournament |
| `STAGE` | Tournament stage (group stage, knockout rounds, champion, …) |
| `GROUP` | Group-stage pool |
| `ROUND` | Round bucket |
| `MATCH` | Fixture |
| `TEAM` | National team |
| `EVENT` | Polymarket event |
| `MARKET` | Polymarket market |
| `OUTCOME` | Market outcome (may carry a compiled `Proposition`) |
| `CONSTRAINT` | N-ary logical constraint (e.g. `EXACTLY_ONE` partition) |

## Edge types

| Type | Role |
| --- | --- |
| `PART_OF` | Structural containment |
| `HAS_MARKET` | Event → market |
| `HAS_OUTCOME` | Market → outcome |
| `PARTICIPATES_IN` | Team participation |
| `PRICES` | Market prices an outcome (or entity) |
| `QUALIFIES_FOR` | Qualification / advancement eligibility |
| `ADVANCES_TO` | Progression between matches/stages |
| `IMPLIES` | Logical implication between markets/outcomes |
| `REFERS_TO` | Outcome proposition argument → topology entity |
| `EQUIVALENT` | Logically equivalent outcomes |
| `COMPLEMENT` | Binary YES/NO complements |
| `MUTEX` | Mutually exclusive outcomes |
| `EXACTLY_ONE` | Constraint → member outcome of an exclusive partition |

Progression edge types: `ADVANCES_TO`, `QUALIFIES_FOR`.

Logical edge types: `IMPLIES`, `EQUIVALENT`, `COMPLEMENT`, `MUTEX`, `EXACTLY_ONE`.

## Allowed edge patterns

| Edge type | Allowed `(source → target)` |
| --- | --- |
| `PART_OF` | STAGE→COMPETITION, GROUP→COMPETITION, ROUND→COMPETITION, MATCH→STAGE, MATCH→GROUP, MATCH→ROUND, GROUP→STAGE, ROUND→STAGE |
| `HAS_MARKET` | EVENT→MARKET |
| `HAS_OUTCOME` | MARKET→OUTCOME |
| `PARTICIPATES_IN` | TEAM→COMPETITION, TEAM→GROUP, TEAM→MATCH, TEAM→STAGE, TEAM→ROUND |
| `PRICES` | MARKET→TEAM, MARKET→MATCH, MARKET→OUTCOME |
| `QUALIFIES_FOR` | TEAM→STAGE, TEAM→ROUND, TEAM→GROUP, MATCH→ROUND |
| `ADVANCES_TO` | MATCH→MATCH, TEAM→ROUND, TEAM→STAGE, STAGE→STAGE |
| `IMPLIES` | MARKET→MARKET, OUTCOME→OUTCOME, MARKET→OUTCOME |
| `REFERS_TO` | OUTCOME→TEAM, OUTCOME→MATCH, OUTCOME→STAGE, OUTCOME→COMPETITION, OUTCOME→GROUP |
| `EQUIVALENT` | OUTCOME→OUTCOME |
| `COMPLEMENT` | OUTCOME→OUTCOME |
| `MUTEX` | OUTCOME→OUTCOME |
| `EXACTLY_ONE` | CONSTRAINT→OUTCOME |

Edges that violate these patterns (or fail confidence / evidence checks) are
written to `rejected_edges.parquet`.

## Proposition layer

Compiled markets attach a formal `Proposition` to `OUTCOME` nodes:

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

`REFERS_TO` edges connect each outcome to the topology entities named in its
arguments. Deterministic rules then emit `IMPLIES` / `EQUIVALENT` / `MUTEX`
edges between outcomes; `COMPLEMENT` and `EXACTLY_ONE` are emitted at compile
time from market structure. Match-result `EXACTLY_ONE` partitions require the
draw market in addition to the two team moneylines — team-only moneylines do
not claim exclusivity because a soccer match can still draw.

## See also

- [Output artifacts](output-artifacts.md)
- [Entity resolution](../concepts/entity-resolution.md)
- [Architecture](../concepts/architecture.md)
