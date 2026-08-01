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

The **Graph** route is a standalone plotter: it omits the product header,
navigation, explanatory sections, and footer so only the graph, its controls,
and selection inspector remain. It opens on every display-essential relation,
including the cross-team winner exclusions. Complete live and static views retain
all teams from the canonical input even when a filter temporarily leaves a team
without a visible edge. Search focuses the selected outcome and its immediate
logical neighbor. IDs, proof tools, evidence filters, confidence, layout
controls, and recording remain progressively disclosed under the selection
panel or **More** menu.

Sigma and Graphology are constructed once per canvas. Inspection state is
applied with reducers, so selection and hover do not replace the renderer or
reset the camera. Complete live and static WC2026 views use frozen team rows with
market close time increasing from left to right; equal close times are placed
in stable market-ID order. Source `end_date` is presentation metadata only and
is excluded from relationship inference. Component overviews remain packed atlases;
event group views pack component groups after an exactly 250-iteration, worker-backed
ForceAtlas2 layout, with deterministic seeds, four-decimal coordinates, and a
session cache. Reduced-motion clients skip placement tweens.

“Auto story” asks the local service for the fingerprint-bound recording plan
and opens the same presentation used by the recorder. Preview controls provide
play/pause, seeking, previous/next highlight, regeneration at the current
confidence threshold, and exit. Intro and outro are text-led; each highlight
shows only its selected claims and bounded context.

`explorer-export --scope graph` writes a `static-explorer-v4` copy of the
complete exported WC2026 scope plus the bundled DuckDB-Wasm client. The static
manifest declares capabilities explicitly. Hierarchy, search, relationship
inspection, the Graph route, and direct comparison remain available; proof,
why-not, story regeneration, and recording controls are omitted. Older static
schemas are rejected with regeneration guidance.
