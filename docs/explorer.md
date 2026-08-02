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

The service's 27 routes are defined by named Pydantic response contracts. The
schema-only application exports the canonical OpenAPI document without opening
an operator graph directory, and the web client derives its API types from that
document. After changing an HTTP contract, regenerate and verify the checked-in
contract before committing:

```bash
cd web
npm run generate:api
npm run check:api
```

The **Graph** route is a standalone plotter: it omits the product header,
navigation, explanatory sections, and footer so only the graph, its controls,
and selection inspector remain. It opens on every display-essential relation,
including the cross-team winner exclusions. Complete live and static views retain
all teams from the canonical input even when a filter temporarily leaves a team
without a visible edge, while relation-specific views omit irrelevant outcomes
and keep one deterministic team representative. Relation, evidence, confidence, and progression-polarity
filters run before redundant implications are removed, so every omitted visible
implication still has an equal-or-stronger path in the filtered graph. Static
snapshots retain all accepted relationship details and derive the same essential
projection in the browser. Accepted compatibility details remain available to
relationship lookup and comparison, but the lean graph excludes them from its
visible edge set. Search focuses the selected outcome and its immediate
logical neighbor. IDs, proof tools, evidence filters, confidence, layout
controls, and recording remain progressively disclosed under the selection
panel or **More** menu.

Sigma and Graphology are constructed once per canvas. Inspection state is
applied with reducers, so selection and hover do not replace the renderer or
reset the camera. When every market has source `end_date`, complete live and
static WC2026 views use frozen team rows with market close time increasing from
left to right; equal close times are placed in stable market-ID sublanes and
Yes/No outcomes sit on opposite sides of the same close column. Without that
optional metadata they use the same team rows with normalized progression from
left to right. Close time is presentation metadata only and is excluded from
relationship inference. Node size and persistent labels reflect the currently
visible graph, and overview links are subdued until hover or selection so the
cross-team winner constraint remains inspectable without becoming an opaque cable.
Component overviews remain packed atlases;
event group views pack component groups after an exactly 250-iteration, worker-backed
ForceAtlas2 layout, with deterministic seeds, four-decimal coordinates, and a
session cache. Reduced-motion clients skip placement tweens.

“Auto story” asks the local service for the fingerprint-bound recording plan
and opens the same presentation used by the recorder. Preview controls provide
play/pause, seeking, previous/next highlight, regeneration at the current
confidence threshold, and exit. Intro and outro are text-led; each highlight
shows only its selected claims and bounded context.

`explorer-export --scope graph` writes a `static-explorer-v5` copy of the
complete exported WC2026 scope. The manifest binds two dependency-free,
SHA-256-verified JSON payloads: the human Explore data loaded at startup and a
frozen graph payload loaded only when the Graph route opens. Canonical essential
edges, highlights, grouped constraints, display statistics, and coordinates are
published by Python rather than recomputed during static startup. Hierarchy,
search, relationship inspection, the Graph route, and direct comparison remain
available; proof, why-not, story regeneration, and recording controls are
omitted. Older static schemas are rejected with regeneration guidance.
