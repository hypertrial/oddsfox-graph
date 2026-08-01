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

`explorer-export` writes bounded `snapshot_nodes.parquet` and
`snapshot_edges.parquet` plus the bundled DuckDB-Wasm client. Export fails on
truncation. Component/event edges are aggregates for navigation; proofs and
conditionals always use accepted proposition-level edges.
