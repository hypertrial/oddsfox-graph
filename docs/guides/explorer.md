---
description: Launch the local Dash and Cytoscape explorer over OddsFox Graph nodes and edges parquet exports.
---

# Explorer

Launch a local, read-only Dash + Cytoscape explorer over exported graph
artifacts.

## Install and start

```bash
uv sync --extra explore
oddsgraph odds-history   # optional; enables the Knockout time slider
oddsgraph explore
```

Opens `http://127.0.0.1:8050` by default. Options: `--host`, `--port`, `--debug`,
plus the shared `--build-dir`.

Requires `build/nodes.parquet` and `build/edges.parquet` from a prior
`oddsgraph build` / `oddsgraph run`. The temporal color slider additionally
needs `build/odds_history.parquet` from `oddsgraph odds-history` (also
produced by `oddsgraph run`).

## Default view

The **knockout bracket** is a classic left-to-right tournament:

- Exactly 32 `MATCH` cards connected by `ADVANCES_TO` edges
- Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final / Third Place
- Non-interactive column headers label each stage across the canvas
- Deterministic `preset` layout (not a force-directed hairball)
- Orthogonal taxi edges without repeated `ADVANCES_TO` labels
- Click a match to highlight its path through the DAG and inspect features
- Hourly **Knockout time** slider colors each match by team-to-advance
  probability (green = home favored, red = away). After a finished/resolved
  match, the winner locks to probability 1; live series keep market odds
  (no lock from the last observed hour alone)

### Progressive controls

- Primary: Knockout time slider, Reset
- Advanced (collapsed by default): confidence and inference-method filters
- Hover a card for a compact preview; the inspector shows identity, provenance,
  and evidence (long market-id lists stay collapsed)

Use the **Controls** / **Inspector** toggles on any viewport width so the
canvas can reclaim space when a sidebar is collapsed.

## See also

- [Analysts](../audiences/analysts.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Running the pipeline](running-the-pipeline.md)
