---
description: Known OddsFox Graph caveats including proposition template coverage and WC2026-only scope.
---

# Known limitations

A single place for caveats that are otherwise scattered across guides and
audience pages. Read this before treating any exported artifact as more
authoritative than it is.

## Proposition bridge covers deterministic templates only

`REFERS_TO`, `PRICES`, `COMPLEMENT`, and `EXACTLY_ONE` edges (plus
`Proposition` payloads on `OUTCOME` nodes) are emitted by the deterministic
proposition compiler for:

- match moneylines / draw / team-to-advance
- group-winner markets
- stage-of-elimination markets
- world-cup-winner markets
- nation/team reaches-stage markets (`Nation to Reach Final/Semifinals/…`,
  `Team to advance to Knockout Stages`)

Match-result `EXACTLY_ONE` partitions require the draw market in addition to
the two team moneylines — team-only moneylines do not claim exclusivity
because a soccer match can still draw. See
[Logical layer](../guides/logical-layer.md) for the full predicate and edge
emission table.

Residual / unrecognized market types still produce `EVENT` / `MARKET` /
`OUTCOME` structure without formal propositions, so those outcomes remain
disconnected from topology entities. Check `proposition_json` on exported
nodes and `derivation_type` on edges before assuming a logical bridge exists.

## Rule engine is intentionally small

Direct logical edges (`IMPLIES`, `EQUIVALENT`, `MUTEX`) come from a fixed
WC2026 rule registry — not from LLM judgment. Transitive `IMPLIES` closure is
**on-demand** via `oddsgraph closure` and is not written into
`edges.parquet` by default. See [Logical layer](../guides/logical-layer.md)
for the registry table and flag interactions.

## Explorer future-round projection is a heuristic

Unresolved future match cards are populated by picking the most likely team
from each feeder branch (`P(reach displayed round)`), then normalizing
conditional stage ratios `P(reach next) / P(reach current)` for the displayed
pair. When stage-reach odds are missing for either team in a feeder, projection
falls back to that feeder's direct `soccer_team_to_advance` series rather than
inventing schedule-home favorites; if both stage-reach and advance odds are
unavailable the slot stays unresolved. That is **not** a
coherent joint bracket model: independence is assumed, Third Place has no
dedicated reach market, and missing/zero stage odds mark probabilities
unavailable rather than inventing 50/50. Prefer resolved match results and
direct advance series when both exist. Curated schedule `winner_team` overlays
(Final champion / Third Place) take precedence over soft odds-history locks in
the explorer so Spain / England remain authoritative once those cards lock.

## Explorer odds charts are hourly-grain

Match-card sparklines and the click-to-expand odds chart sample the same hourly
parquet series used for projection (`odds_history.parquet` /
`stage_odds_history.parquet`). A live ~2–3 hour match therefore yields only a
handful of points, and there are **no in-play goal-event markers** in the data
model. When the direct match series has no points at or before the selected
hour (missing, or only future points), trends fall back to each projected
team's stage-reach probability for the displayed round — that is **not** the
eventual head-to-head advance market. After full-time, sparkline endpoints lock
to the same 100% / 0% winner probabilities shown on the card.

## Scope is WC2026 / Polymarket only

Deterministic templates, team alias/code tables, proposition predicates, and
the official bracket fragment (`oddsgraph/data/wc2026_schedule.json`) are all
built against the Polymarket WC2026 hourly-odds schema and FIFA's 2026 World
Cup structure. Nothing here generalizes to other tournaments or other
prediction-market platforms without new templates, alias tables, and a new
schedule export.

## Residual LLM output is lower-confidence than deterministic/official paths

Events not covered by deterministic templates or the official bracket go
through chunked local LLM extraction. This path is inherently less reliable
than template-driven extraction. Always check `inference_method` and
`confidence` before trusting an edge or node, and prefer filtering with
`--minimum-confidence` for downstream use. See
[Entity resolution](entity-resolution.md) and
[Integrators](../audiences/integrators.md).

## Fine-tuning scripts are experimental

`scripts/export_finetune_dataset.py`, `scripts/finetune_lora.py`, and
`scripts/eval_finetuned_model.py` are not part of the stable CLI surface.
Flags and output paths may change without the same compatibility
expectations as `oddsgraph`'s core commands. See
[Fine-tuning](../guides/finetuning.md).

## No hosted service or bundled data

OddsFox Graph is Hypertrial-owned MIT **software**, not a hosted dataset or
API. You must supply your own Pipeline golden mart export and local model
weights; nothing here is fetched or served on your behalf.

## Apple Silicon is the primary tested environment

Backend performance guidance (`inprocess`, `mlx`, `llama-server` tuning) is
based on Apple Silicon + Metal measurements. CPU-only / Linux paths work
(see [Linux / CPU-only setup](../guides/linux-cpu-setup.md)) but are
exercised mainly through CI correctness checks, not performance
benchmarking.

## See also

- [Logical layer](../guides/logical-layer.md)
- [FAQ](faq.md)
- [Troubleshooting](troubleshooting.md)
- [Architecture](architecture.md)
