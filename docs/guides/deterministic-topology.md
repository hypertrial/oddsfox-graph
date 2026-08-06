# Deterministic topology

By default, `infer` / `run` classify each event from structured Polymarket
fields before any LLM call. Covered events skip LLM chunking entirely and are
recorded as `deterministic` (or verified/corrected variants) in
`inference_report.json`.

On WC2026 data this covers roughly 91% of events and cuts estimated LLM chunk
volume by about 15×.

## Templates

| Template | Example `event_title` | Extracted topology |
| --- | --- | --- |
| Match | `Brazil vs. Morocco - Exact Score` | TEAM ×2, MATCH, PARTICIPATES_IN |
| Group winner | `World Cup Group D Winner` | TEAM, GROUP, PARTICIPATES_IN |
| Stage of elimination | `World Cup: Portugal Stage of Elimination` | TEAM, STAGE, QUALIFIES_FOR |
| Tournament winner | `World Cup Winner` | TEAM, STAGE(Champion), QUALIFIES_FOR |

## Team canonicalization

Team names are canonicalized via `oddsgraph/data/team_name_aliases.json` (for
example `Korea Republic` → `South Korea`, `IR Iran` → `Iran`) so group membership
and match nodes merge cleanly.

FIFA / Polymarket team codes live in `oddsgraph/data/team_codes.json`. Note that
Polymarket WC2026 slugs use `kor` for **Curaçao** and `kr` for **South Korea**.

## Player props

Player-prop markets (`soccer_player_*`) still get MARKET / OUTCOME nodes, but
add no extra topology beyond the match pairing. Unrecognized events (for example
Golden Ball or fun props) continue through the chunked LLM path.

## Escape hatch

```bash
oddsgraph infer --no-deterministic-topology
```

Optional LLM confirm/patch over deterministic output:

```bash
oddsgraph infer --verify-deterministic
```

Verified artifacts land at `build/fragments/<event_id>__verified.json` with a
sidecar `__verify_manifest.json` that fingerprints the template candidate. With
`--resume` (default), verified files are reused only when that fingerprint still
matches the current template; otherwise verification runs again. On `build`,
verified topology **replaces** template topology for that event
(EVENT/MARKET/OUTCOME base nodes remain deterministic).

## See also

- [Running the pipeline](running-the-pipeline.md)
- [Entity resolution](../concepts/entity-resolution.md)
- [Architecture](../concepts/architecture.md)
