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

The **knockout bracket** (32 `MATCH` nodes connected by `ADVANCES_TO` edges):

- Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final / Third Place
- Laid out as a top-to-bottom DAG with dagre

Switch **View → Full topology** for the broader
`COMPETITION` / `STAGE` / `GROUP` / `ROUND` / `MATCH` / `TEAM` graph (~180 nodes
on a full WC2026 build).

Use the search box to pull in `EVENT` / `MARKET` / `OUTCOME` nodes and expand
their neighbors; the type filter auto-enables added types so results stay
visible. Click any node or edge to inspect exported features (confidence,
aliases, evidence market IDs, inference/resolution methods, evidence text).

## Topology / market disconnect

!!! warning

    The topology layer and the market layer are currently **disconnected**. The
    export has no `PRICES` or `IMPLIES` edges linking `MATCH` / `TEAM` nodes to
    `EVENT` / `MARKET` / `OUTCOME` nodes, so expanding a topology node will not
    reach markets. Search for an event title (or market id) to explore the market
    layer independently.

## See also

- [Analysts](../audiences/analysts.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Running the pipeline](running-the-pipeline.md)
