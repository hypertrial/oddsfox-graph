# Logic Explorer

The explorer is designed for a catalog much larger than a browser can safely
render at once. It opens at component or event level and drills into bounded
proposition neighborhoods.

## Local service

```bash
oddsfox-graph serve --out output/graph --open-browser
```

The packaged React/Sigma client provides:

- event and connected-component overviews with deterministic coordinates;
- proposition search and bounded one-to-four-hop expansion;
- relation, confidence, and compatibility filters;
- coverage and truncation warnings;
- node, aggregate edge, and accepted-proposal provenance;
- implication proofs and why-not diagnostics.

The FastAPI layer opens the final DuckDB file read-only. It uses parameterized
queries, stable cursor ordering, and hard response ceilings, including node
detail edge lists and neighborhood seed sets. The host must be
loopback. Security headers deny framing and external scripts; there is no
mutation or arbitrary SQL endpoint.

## Static snapshots

`explorer-export` packages the same client with bounded `snapshot_nodes.parquet`
and `snapshot_edges.parquet`. DuckDB-Wasm loads those files in the browser. The
export manifest binds the source graph, scope, identifier, ceilings, snapshot
hash, and coverage. Export fails on truncation so a portable snapshot never
silently claims completeness. Relation and confidence filters continue to work
inside a static snapshot; its exported graph level is fixed.
The destination must be separate from—and not nested inside—the source graph
directory so export publication cannot modify the completed discovery output.
Serve the directory over local HTTP; browser worker and Wasm security policies
do not support opening the generated `index.html` through a `file:` URL.

## Interpretation

Component and event edges are aggregates. They help navigate the graph but do
not add logical conclusions. Select a proposition edge for complete consensus,
solver, citation, confidence, and model fingerprints. The coverage indicator is
the assessed share of eligible retrieved pairs—not the share of all possible
catalog pairs.
