---
description: Column contracts for nodes, edges, rejected edges, ontology.json, and inference_report.json exports.
---

# Output artifacts

Default export directory: `build/`.

## `nodes.parquet`

Schema from `CanonicalNode`:

| Column | Type | Description |
| --- | --- | --- |
| `canonical_id` | string | Stable resolved node id |
| `type` | string | `NodeType` value |
| `label` | string | Display label |
| `aliases` | list[string] | Alternate labels |
| `confidence` | float | `[0, 1]` |
| `evidence_market_ids` | list[string] | Supporting market ids |
| `resolution_method` | string | How the node was resolved |
| `inference_method` | string | How the node was inferred |
| `proposition_json` | string \| null | JSON-serialized `Proposition` for compiled OUTCOME nodes |

## `edges.parquet`

Schema from `CanonicalEdge`:

| Column | Type | Description |
| --- | --- | --- |
| `source_id` | string | Canonical source node id |
| `target_id` | string | Canonical target node id |
| `edge_type` | string | `EdgeType` value |
| `confidence` | float | `[0, 1]` |
| `evidence_market_ids` | list[string] | Supporting market ids |
| `evidence_text` | string | Optional evidence snippet |
| `inference_method` | string | How the edge was inferred |
| `derivation_type` | string | `extraction`, `compiler`, `rule`, or `transitive` |
| `rule_id` | string \| null | Rule identifier when `derivation_type=rule` |
| `rule_version` | int \| null | Rule version when `derivation_type=rule` |
| `premises` | list[string] \| null | Proposition keys or transitive path |

## `rejected_edges.parquet`

Same columns as edges, plus:

| Column | Type | Description |
| --- | --- | --- |
| `rejection_reason` | string | Why the edge was dropped |

Known `rejection_reason` values:

| Reason | Meaning |
| --- | --- |
| `implies_cycle` | Accepting the edge would introduce a cycle in the `IMPLIES` DAG |
| `below_minimum_confidence` | Edge confidence is below `--minimum-confidence` |
| `invalid_confidence` | Confidence is missing or outside `[0, 1]` |
| `missing_evidence` | Required evidence fields are empty |
| `missing_endpoint` | `source_id` or `target_id` is not present in the node set |
| `invalid_pattern` | `(source_type, edge_type, target_type)` is not in the ontology |
| `progression_cycle` | Accepting the edge would cycle progression (`ADVANCES_TO` / `QUALIFIES_FOR`) |

## `implies_closure.parquet`

Optional on-demand export from `oddsgraph closure`. Same columns as
`edges.parquet`; rows are transitive `IMPLIES` edges with
`derivation_type=transitive` and `premises` holding the shortest path.

## `odds_history.parquet`

Optional export from `oddsgraph odds-history` (also produced by `oddsgraph run`).
Hourly knockout win probabilities from Polymarket `soccer_team_to_advance`
markets.

| Column | Type | Description |
| --- | --- | --- |
| `match_canonical_id` | string | Knockout MATCH id |
| `home_team` | string | Canonical home team label |
| `away_team` | string | Canonical away team label |
| `odds_hour_epoch` | int64 | Hour bucket start (UTC epoch seconds) |
| `home_prob` | float | Probability home advances / wins |
| `away_prob` | float | Probability away advances / wins |
| `match_start_epoch` | int64 | Kickoff / game start epoch |
| `match_end_epoch` | int64 \| null | Match finished epoch when known; null for live series |
| `winner_team` | string \| null | Locked winner when finished/resolved; null while live |

## `stage_odds_history.parquet`

Optional sibling export from the same `oddsgraph odds-history` command.
Hourly team probabilities for reaching knockout stages and winning the
tournament (World Cup Winner markets, stored as stage label `Champion`).

| Column | Type | Description |
| --- | --- | --- |
| `team` | string | Canonical team display name |
| `stage_label` | string | Reach stage (`Round of 32` … `Final`) or `Champion` |
| `odds_hour_epoch` | int64 | Hour bucket start (UTC epoch seconds) |
| `reach_prob` | float | Probability the team reaches that stage / wins |
| `market_id` | string | Source Polymarket market id |

The explorer uses these series to project unresolved future matchups and to
estimate conditional advance probabilities when direct head-to-head advance
markets are unavailable.

## `ontology.json`

Dump of node types, edge types, allowed patterns, progression edge types, and
logical edge types from `dump_ontology_json()`.

## `inference_report.json`

Schema from `InferenceReport`:

| Field | Description |
| --- | --- |
| `model_path` | Model used, if any |
| `events_processed` / `events_failed` / `events_skipped` | Aggregate counts |
| `events_deterministic` / `events_deterministic_verified` / `events_deterministic_corrected` | Deterministic path counts |
| `node_counts` / `edge_counts` | Type histograms |
| `resolution_tiers` | Resolution method counts |
| `rejected_edge_reasons` | Rejection reason histogram |
| `per_event_status` | Map of event id → status string |

## Intermediate artifacts

| Path | Description |
| --- | --- |
| `semantic_markets.parquet` | Reduced market records |
| `fragments/<event_id>.json` | Per-event residual graph fragments |
| `fragments/<event_id>__topology.json` | Deterministic topology from infer; build reuses these instead of reclassifying covered events |
| `fragments/<event_id>__verified.json` | Opt-in verified/corrected topology; on build these **replace** template topology for that event |
| `fragments/<event_id>__verify_manifest.json` | Fingerprint of the template candidate used for resume invalidation |

Fragment filenames use a single path-safe `event_id` segment matching
`[A-Za-z0-9._-]+` **without** embedded `__` and without surrounding whitespace.
IDs with path separators, spaces, `..`, or `__` are rejected so writes cannot
escape `build/fragments/` or collide with reserved suffixes such as
`__verified.json` / `__topology.json` / `__partN.json`.

`oddsgraph odds-history` / `run` scan the hourly source mart once and write both
`odds_history.parquet` and `stage_odds_history.parquet`.

## See also

- [Ontology](ontology.md)
- [Logical layer](../guides/logical-layer.md)
- [Explorer](../guides/explorer.md)
- [Integrators](../audiences/integrators.md)
