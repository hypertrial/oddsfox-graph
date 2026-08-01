# Architecture

One typed staged pipeline owns both discovery modes:

1. normalize the compact catalog and create every selected source proposition;
2. run the strict deterministic extractor with literal source spans;
3. generate bounded structured candidates in DuckDB;
4. derive and validate deterministic proofs;
5. optionally enrich unresolved candidates with ANN, NLI, and dual models;
6. solve proposal-connected components with RC2;
7. aggregate explorer summaries and atomically publish the manifest last.

`FastModePolicy` enables stages 1–4, 6, and 7 and forbids inference resources.
`FullModePolicy` adds stage 5 and requires an exact automation profile. Mode
selection happens at the orchestration boundary; the graph, solver, publication,
query, report, and explorer contracts are shared.

## Deterministic core

Authoritative IDs, outcomes, text, event metadata, and timestamps always create
nodes. Extractors return `exact`, `ambiguous`, or `unmatched` and preserve exact
question/description spans. Only exact fields participate in cross-market rules.
Candidates are blocked by market/event, resolution, numeric, temporal, stage,
and strict singular-winner signatures. Cross-event rules require a complete
rule-specific proof-scope key; fuzzy entity merging is never used.

## Full enrichment

MiniLM vectors are inserted into a single-threaded USearch HNSW index in stable
proposition order. The pipeline queries 64 neighbors, recomputes exact cosine
scores, and deterministically retains the top 20. NLI can prioritize or veto but
cannot publish. A model edge requires matching Qwen/Granite relation and
direction, valid citations, no assumptions, calibrated confidence, no NLI veto,
and solver acceptance.

SQLite owns transactional inference cache entries. DuckDB owns propositions,
candidates, proofs, solver state, aggregates, layouts, and the public database.
Incremental baselines are current, manifest-complete v0.11 outputs only.

## Exploration

`Graph` and `ExplorerStore` open completed outputs read-only. The loopback-only
FastAPI service exposes bounded parameterized queries, never arbitrary SQL.
React/Sigma starts at component or event summaries, then requests bounded
proposition neighborhoods. `prove` traverses implication plus bidirectional
equivalence only; it never materializes transitive closure.

## Presentation and recording

`Graph.recording_plan()` and `GET /api/v1/recording-plan` share the same frozen
contracts. Ranking and context selection run as bounded, stable DuckDB queries;
layout remains a browser concern and never adds work to fast or full discovery.
The browser exposes only `ready`, `getStory()`, `getFrameCount()`, and
idempotent `seek(frame)` for automation. Each seek synchronously derives the
camera, reducers, caption, and overlays from the frame number.

The Python recorder starts the existing loopback app on an ephemeral port,
blocks non-loopback browser requests, and streams each PNG directly to an
FFmpeg `image2pipe` process. Frames are never accumulated on disk or in memory.
Chromium, FFmpeg, and the server are closed on failure or cancellation; the
sibling staging directory is removed unless all validation and hashes succeed.
