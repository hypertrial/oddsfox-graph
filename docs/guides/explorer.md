---
description: Launch the local Dash and Cytoscape explorer over OddsFox Graph nodes and edges parquet exports.
---

# Explorer

Launch a local, read-only Dash + Cytoscape explorer over exported graph
artifacts.

## Install and start

```bash
uv sync --extra explore
oddsgraph explore
```

Opens `http://127.0.0.1:8050` by default. Options: `--host`, `--port`, `--debug`,
plus the shared `--build-dir`.

Requires `build/nodes.parquet` and `build/edges.parquet` from a prior
`oddsgraph build` / `oddsgraph run`.

## Default view

The **knockout bracket** is a classic left-to-right tournament:

- Exactly 32 `MATCH` cards connected by `ADVANCES_TO` edges
- Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final / Third Place
- Deterministic `preset` layout (not a force-directed hairball)
- Orthogonal taxi edges without repeated `ADVANCES_TO` labels
- Click a match to highlight its path through the DAG and inspect features

Switch **View → Full topology** for the broader
`COMPETITION` / `STAGE` / `GROUP` / `ROUND` / `MATCH` / `TEAM` graph (~180 nodes
on a full WC2026 build).

### Progressive controls

- Primary: View, Search, Reset
- Advanced (collapsed by default): node-type filter, confidence, inference
  method, free layout chooser
- Search or **Expand neighbors** from the bracket switches into Full topology
  so the 32-node preset stays clean
- Hover a card for a compact preview; the inspector shows identity, provenance,
  and evidence (long market-id lists stay collapsed)

On narrower viewports, use the **Controls** / **Inspector** toggles so the
canvas keeps most of the screen.

## Topology / market bridge

!!! note

    Deterministic proposition compilation links covered `OUTCOME` nodes into
    topology via `REFERS_TO` (plus `PRICES` / logical edges). Residual market
    types without a compiled proposition remain disconnected — search for an
    event title or market id to inspect them, and see
    [Known limitations](../concepts/limitations.md).

## See also

- [Analysts](../audiences/analysts.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Running the pipeline](running-the-pipeline.md)
