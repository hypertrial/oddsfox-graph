---
description: Launch the local Dash knockout-tree explorer over OddsFox Graph nodes and edges parquet exports.
---

# Explorer

Launch a local, read-only Dash explorer over exported graph artifacts. The
knockout stage is rendered as a mirrored tournament tree (website-style cards
and SVG connectors) with a time-slider-driven odds animation.

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
official schedule kickoffs through Final full-time. Future-round projections
and advance probabilities additionally need:

- `build/odds_history.parquet` (direct match advance odds)
- `build/stage_odds_history.parquet` (team stage-reach / champion odds)

Both are produced by `oddsgraph odds-history` and by `oddsgraph run`.

## Default view

The **knockout bracket** is a mirrored two-halves tree on a dark sports-data
canvas (desktop ≥1400px). Narrower viewports use stacked rounds:

- Knockout `MATCH` cards connected by `ADVANCES_TO` edges (32 on a full
  official build)
- Round of 32 on both outer edges → Round of 16 → Quarterfinals → Semifinals →
  centered Final / Third Place / predicted champion column
- Each card shows **country flags**, team names, and **both teams’ advance
  probabilities** (or `—` when unavailable)
- Pill legend: Resolved / Pending / Diverged
- A persistent **phase tracker** (Groups → Final weekend) highlights the
  schedule window for the selected hour, including intermissions
- Bottom **playback dock** (full-width footer below the scrollable bracket):
  compact UTC time (`Jun 28 · 19:00 UTC`), phase badge, game-milestone slider,
  Play/Pause, Reset view, and live action status. The Filters drawer overlays
  the bracket stage only, so playback stays usable while filters are open.

### Projection rules

At the selected hour:

1. Resolved matches keep actual participants and lock the winner to probability 1.
2. Unresolved future slots pick the most likely team from each feeder branch
   using `P(reach displayed round)`. When stage-reach odds are missing for
   either team in the feeder, the feeder's direct advance series is used
   instead; if both stage-reach and advance odds are unavailable the slot
   stays unresolved rather than inventing schedule-home favorites.
3. Each displayed team’s advance score is
   `P(reach next round) / P(reach displayed round)`, then the pair is
   normalized to 100%. The Final uses `P(win tournament) / P(reach Final)`.
4. Third Place projects the most likely semifinal loser from each branch; it
   uses direct matchup odds when available, otherwise shows an explicit
   unavailable probability (`—`) rather than inventing 50/50 odds.
5. Dashed borders mark projected (not yet locked) matchups. Resolved cards use
   green probability styling; pending cards use cyan; unavailable paths use gray.
6. When a match is locked, cards show **100%** / **0%** for the winner and
   loser.
7. When the scrubbed hour lands exactly on a match’s full-time milestone, that
   card (and any simultaneous fixtures sharing the step) shows a **Just
   finished** badge and pulse highlight until the next milestone.

### Progressive controls

- Primary: playback dock (tournament start → Final full-time), Play
  (one kickoff or full-time per step; ~184 steps end-to-end; simultaneous
  fixtures share a step), Reset view. Manual scrubbing snaps to the same
  game milestones.
- **Filters & legend** drawer (closed by default): confidence / inference
  filters, pill legend, projection help, and data-source metadata
- Phase tracker stays visible on laptop and tablet widths; narrow viewports
  use abbreviated labels with full accessible names

During Group Stage the tracker says the knockout bracket is projected. Between
knockout rounds it shows the next stage (for example “Quarterfinals next”).
Third Place and Final share the Final weekend tracker step, with the precise
subphase in the playback badge.

## See also

- [Analysts](../audiences/analysts.md)
- [Output artifacts](../reference/output-artifacts.md)
- [Running the pipeline](running-the-pipeline.md)
- [Known limitations](../concepts/limitations.md)
