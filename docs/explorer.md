# FIFA World Cup 2026 Outcome Map

```bash
oddsfox-graph serve --out output/fast --open-browser
```

The default Explore experience is a human-readable map of the pipeline's
Polymarket WC2026 team-progression markets. It starts with tournament stages,
teams, notable relationships, contextual search, and outcome comparison. It
does not open on a raw network. The header states the exact universe and never
describes it as every World Cup market.

Routes are shareable in the live service and standalone export:

- `#/explore`
- `#/explore/stage/{stageKey}`
- `#/explore/team/{teamKey}`
- `#/explore/market/{marketId}`
- `#/explore/relationship/{proposalId}`
- `#/compare`
- `#/analyst`

Team pages render a deterministic progression ladder. Binary Yes/No claims are
one market card; relationship pages show two contextual claims and a plain
explanation. IDs, canonical propositions, hashes, coordinates, and raw
provenance remain in closed technical details. Human views never display
internal `NOT(...)` labels.

The loopback FastAPI service opens DuckDB read-only, uses parameterized stable
queries, enforces node/edge ceilings, and exposes no arbitrary SQL or mutation.
Search, comparison, relation/confidence/evidence filters, provenance, proofs,
and why-not diagnostics are mode-aware. In fast mode, a missing semantic edge
reports `full_mode_not_run` or `not_applicable_to_deterministic_rules`; it is
never silently labeled unrelated.

The **Analyst graph** route retains Sigma, semantic zoom, filters, inspection,
proofs, and why-not tools. Explore requests display-essential edges and reports
hidden transitive implications. Analyst requests every accepted edge. Dense
Explore context is grouped instead of drawn; a nearby network is allowed only
at 15 nodes, 24 edges, density 0.15 or below, label uniqueness 0.50 or above,
and maximum degree 8 or below.

Sigma and Graphology are constructed once per canvas. Inspection state is
applied with reducers, so selection and hover do not replace the renderer or
reset the camera. Analyst component overview is a packed atlas. Event and
proposition views pack component groups after an exactly 250-iteration,
worker-backed ForceAtlas2 layout, with deterministic seeds, four-decimal
coordinates, and a session cache. Reduced-motion clients skip placement
tweens.

“Auto story” asks the local service for the fingerprint-bound recording plan
and opens the same presentation used by the recorder. Preview controls provide
play/pause, seeking, previous/next highlight, regeneration at the current
confidence threshold, and exit. Intro and outro are text-led; each highlight
shows only its selected claims and bounded context.

`explorer-export --scope graph` writes a `static-explorer-v3` copy of the
complete exported WC2026 scope plus the bundled DuckDB-Wasm client. The static
manifest declares capabilities explicitly. Hierarchy, search, relationship
inspection, Analyst graph, and direct comparison remain available; proof,
why-not, story regeneration, and recording controls are omitted. Older static
schemas are rejected with regeneration guidance.
