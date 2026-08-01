# Logic Explorer

```bash
oddsfox-graph serve --out output/fast --open-browser
```

The header prominently shows `fast / DETERMINISTIC_VALIDATED` or `full /
EXPERIMENTAL_FULL`. Evidence filters distinguish source-contract facts, strict
deterministic proofs, and generative consensus. The explorer begins with event
or component aggregates and requests bounded proposition neighborhoods only.

The loopback FastAPI service opens DuckDB read-only, uses parameterized stable
queries, enforces node/edge ceilings, and exposes no arbitrary SQL or mutation.
Search, relation/confidence/evidence filters, provenance, proofs, and why-not
diagnostics are mode-aware. In fast mode, a missing semantic edge reports
`full_mode_not_run` or `not_applicable_to_deterministic_rules`; it is never
silently labeled unrelated.

Sigma and Graphology are constructed once per canvas. Inspection state is
applied with reducers, so selection and hover do not replace the renderer or
reset the camera. Component overview is a packed atlas. Event and proposition
views pack component groups after an exactly 250-iteration, worker-backed
ForceAtlas2 layout, with deterministic seeds, four-decimal coordinates, and a
session cache. Double-clicking components or events drills through semantic
zoom; breadcrumbs return to the overview. “Re-layout” explicitly recomputes
placement, while reduced-motion clients skip the 600 ms placement tween.

“Auto story” asks the local service for the fingerprint-bound recording plan
and opens the same presentation used by the recorder. Preview controls provide
play/pause, seeking, previous/next highlight, regeneration at the current
confidence threshold, and exit. Static exports cannot request a recording plan.

`explorer-export` writes a `static-explorer-v2` bounded
`snapshot_nodes.parquet` and `snapshot_edges.parquet` plus the bundled
DuckDB-Wasm client. Edge evidence tier is required and preserved. Export fails
on truncation. Component/event edges are aggregates for navigation; proofs and
conditionals always use accepted proposition-level edges.
