---
description: Launch the local Dash and Cytoscape explorer over OddsFox Graph nodes and edges parquet exports.
---

# Explorer

Launch a local, read-only Dash + Cytoscape explorer over exported graph
artifacts.

## Install and start

```bash
uv sync --extra explore
oddsgraph odds-history   # optional; enables projections on the tournament time slider
oddsgraph explore
# or: oddsgraph explore --build-dir path/to/build
# shared globals also work before the subcommand: oddsgraph --build-dir path/to/build explore
```

Opens `http://127.0.0.1:8050` by default. Options: `--host`, `--port`, `--debug`,
and `--build-dir` (also available as a root/global option before the subcommand).

Requires `build/nodes.parquet` and `build/edges.parquet` from a prior
`oddsgraph build` / `oddsgraph run`. The tournament time slider always spans
official schedule kickoffs. Future-round projections and advance probabilities
additionally need:

- `build/odds_history.parquet` (direct match advance odds)
- `build/stage_odds_history.parquet` (team stage-reach / champion odds)

Both are produced by `oddsgraph odds-history` and by `oddsgraph run`.

## Default view

The **knockout bracket** is a classic left-to-right tournament:

- Exactly 32 `MATCH` cards connected by `ADVANCES_TO` edges
- Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final / Third Place
- Each card shows **country flags**, team names, and **advance probabilities**
- Non-interactive column headers label each stage across the canvas
- Deterministic `preset` layout (not a force-directed hairball)
- Orthogonal taxi edges without repeated `ADVANCES_TO` labels
- Click a match to open the inspector and highlight its path through the DAG
- Hourly **Tournament time** slider (official start → end) reprojects unresolved
  future matchups; **Play** advances one hour at a time (one day / 2 seconds)

### Projection rules

At the selected hour:

1. Resolved matches keep actual participants and lock the winner to probability 1.
2. Unresolved future slots pick the most likely team from each feeder branch
   using `P(reach displayed round)`. When stage-reach odds are missing, the
   feeder's direct advance series is used instead; if both are unavailable the
   slot stays unresolved rather than inventing schedule-home favorites.
3. Each displayed team’s advance score is
   `P(reach next round) / P(reach displayed round)`, then the pair is
   normalized to 100%. The Final uses `P(win tournament) / P(reach Final)`.
4. Third Place projects the most likely semifinal loser from each branch; it
   uses direct matchup odds when available, otherwise shows an explicit
   unavailable probability (`—`) rather than inventing 50/50 odds.
5. Dashed borders mark projected (not yet locked) matchups. Soft mint/rose
   tints remain a secondary cue; the numeric percentages are the primary signal.

### Progressive controls

- Primary: Tournament time slider (start → end of the official schedule), Play
  (one hour per step; one day every 2 seconds), Reset
- Advanced (collapsed by default): confidence and inference-method filters
- Hover a card for a compact preview; the inspector opens on selection and
  shows identity, provenance, and evidence

Use the **Controls** / **Inspector** toggles on any viewport width so the
canvas can reclaim space when a sidebar is collapsed.

## See also

- [Analysts](../audiences/analysts.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Running the pipeline](running-the-pipeline.md)
- [Known limitations](../concepts/limitations.md)
