# Entity resolution

After per-event fragments are written, `build` merges them into canonical nodes
and edges (`oddsgraph/resolution.py`, `oddsgraph/schema.py`).

## Fragment merge

`merge_fragments` unions nodes and edges that share local IDs / edge keys:

- evidence market IDs are unioned
- aliases are unioned
- confidence takes the max
- evidence text prefers the higher-confidence edge

## Resolution tiers

Fragment-local nodes map to canonical IDs using ordered tiers, including:

1. Exact Polymarket identifiers (for example `event:`… / market ids)
2. Exact slug / label matches within a node type
3. Alias matches
4. Fuzzy label matches via RapidFuzz (`fuzzy_threshold`, default `92`)

Unresolved nodes keep an unresolved method marker until a later tier succeeds.
Tier counts appear in `inference_report.json` under `resolution_tiers`.

## Confidence filtering

`--minimum-confidence` rejects edges below the threshold during `build`. It does
not change entity resolution itself. Rejected edges land in
`rejected_edges.parquet` with a `rejection_reason`.

## Ontology validation

Edges must match `ALLOWED_EDGE_PATTERNS`. Invalid patterns are rejected even if
confidence is high. See [Ontology](../reference/ontology.md).

## See also

- [Architecture](architecture.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Ontology](../reference/ontology.md)
