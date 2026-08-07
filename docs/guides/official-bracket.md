---
description: Inject the curated FIFA WC2026 stage ladder and knockout MATCH bracket during oddsgraph build.
---

# Official bracket

By default, `build` / `run` inject a curated fragment from
`oddsgraph/data/wc2026_schedule.json` (104 FIFA-reviewed fixtures exported once
from `oddsfox-pipeline`'s OpenFootball warehouse). The curated snapshot includes
completed knockout participants plus explicit Final / Third Place winners
(`Spain` champion, `England` third) so the explorer can lock those cards.

## What gets added

- Stage ladder: `Group Stage → Round of 32 → Round of 16 → Quarterfinals → Semifinals → Final → Champion`, plus `Semifinals → Third Place` (`STAGE ADVANCES_TO STAGE`)
- All 104 matches placed with `MATCH PART_OF STAGE`
- Knockout progression `MATCH ADVANCES_TO MATCH` derived from team continuity across consecutive stages
- `inference_method=official_bracket` on those edges and nodes

## Regenerate the schedule snapshot

Requires a local `oddsfox-pipeline` DuckDB:

```bash
uv run python scripts/export_wc2026_schedule.py
```

The exporter preserves any existing per-fixture `winner_team` overlays (used for
Final / Third Place explorer locks) when rewriting the JSON.

## Escape hatch

```bash
oddsgraph build --no-official-bracket
```

Or on a full run:

```bash
oddsgraph run --no-official-bracket
```

## See also

- [Running the pipeline](running-the-pipeline.md)
- [Ontology](../reference/ontology.md)
- [Architecture](../concepts/architecture.md)
