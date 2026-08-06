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

## `rejected_edges.parquet`

Same columns as edges, plus:

| Column | Type | Description |
| --- | --- | --- |
| `rejection_reason` | string | Why the edge was dropped |

## `ontology.json`

Dump of node types, edge types, allowed patterns, and progression edge types
from `dump_ontology_json()`.

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
| `fragments/<event_id>__verified.json` | Opt-in verified/corrected topology; on build these **replace** template topology for that event |
| `fragments/<event_id>__verify_manifest.json` | Fingerprint of the template candidate used for resume invalidation |

Fragment filenames use a single path-safe `event_id` segment matching
`[A-Za-z0-9._-]+`. IDs with path separators, spaces, or `..` are rejected so
writes cannot escape `build/fragments/` (or nest under unexpected directories).

## See also

- [Ontology](ontology.md)
- [Explorer](../guides/explorer.md)
- [Integrators](../audiences/integrators.md)
