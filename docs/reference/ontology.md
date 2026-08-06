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
| `OUTCOME` | Market outcome |

## Edge types

| Type | Role |
| --- | --- |
| `PART_OF` | Structural containment |
| `HAS_MARKET` | Event → market |
| `HAS_OUTCOME` | Market → outcome |
| `PARTICIPATES_IN` | Team participation |
| `PRICES` | Market prices an entity |
| `QUALIFIES_FOR` | Qualification / advancement eligibility |
| `ADVANCES_TO` | Progression between matches/stages |
| `IMPLIES` | Logical implication between markets/outcomes |

Progression edge types: `ADVANCES_TO`, `QUALIFIES_FOR`.

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

Edges that violate these patterns (or fail confidence / evidence checks) are
written to `rejected_edges.parquet`.

## See also

- [Output artifacts](output-artifacts.md)
- [Entity resolution](../concepts/entity-resolution.md)
- [Architecture](../concepts/architecture.md)
