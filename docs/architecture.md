# Architecture

The repository has one discovery path and one read-only exploration layer.

## Discovery

1. Validate and normalize the compact snapshot.
2. Qualify the exact primary/verifier runtime pair on deterministic cases.
3. Parse each market with both models and merge exact normalized agreement.
4. Retrieve structured and exact blockwise-cosine candidate neighborhoods.
5. Balance the bounded inference queue across event-pair scopes.
6. Apply NLI vetoes and dual generative classification.
7. Quarantine disagreement, assumptions, invalid citations, failures, vetoes,
   and low confidence.
8. Solve deterministic and consensus proposals with RC2.
9. Aggregate events and connected components, compute node metrics and stable
   layouts, then publish DuckDB, sorted Parquet, reports, state, and the final
   manifest completion marker.

SQLite owns transactional inference caching. DuckDB owns candidates, metrics,
components, solver state, exploration tables, and public graph tables. Clean,
cached, offline, and equivalent incremental runs preserve logical artifact hashes.

## Exploration

`Graph` opens only a manifest-complete current output. `ExplorerStore` creates
short-lived read-only DuckDB connections and exposes parameterized, cursor-based,
bounded queries. The FastAPI service binds only to loopback and applies hard
node/edge limits; it offers no arbitrary SQL endpoint and never mutates the graph.

The React/Sigma client requests component or event summaries before proposition
details. Search and neighborhood expansion avoid loading the entire graph in the
browser. Static exports contain only a selected bounded subgraph and use
DuckDB-Wasm to read the exported Parquet files.

`prove` expands implication and bidirectional equivalence arcs on demand. It
never traverses complement, exclusion, or compatibility, never materializes a
transitive closure, and enforces hop, path, generated-state, and per-node
expansion limits.
