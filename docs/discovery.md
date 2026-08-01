# Discovery

`discover` requires `--mode fast|full`; there is no implicit mode. Input profile
`auto` detects exactly one supported schema, while production WC2026 commands
must pass `--input-profile polymarket-wc2026-graph-hourly-v1` explicitly.
The output must not be the input file or an ancestor directory containing it;
discovery rejects either target before publication.

## WC2026 graph input

The human explorer consumes only the pipeline's registry-scoped
`polymarket_wc2026_graph_token_hourly_odds` export. The adapter validates every
hourly row and collapses it into one market with exactly two Yes/No claims. Team,
stage, market direction, progression meaning, token orientation, reciprocal
opposite-token IDs, status, and hourly grain must be complete and unambiguous.
Malformed markets fail the build; discovery never skips them or falls back to
question or slug matching.

Prices and hourly observation times do not participate in logical semantics.
The manifest records the raw file hash as provenance and a separate normalized
semantic fingerprint that is stable across row order and price changes.
`--max-propositions` is invalid for this profile because partial selection can
break team-stage chains. Sparse but structurally valid upstream stage coverage
is retained and reported rather than synthesized.

## Fast mode

Fast accepts `--input`, `--out`, optional `--incremental-from`, optional
`--max-propositions`, `--deadline-seconds` (default 120), `--progress-format`,
and `--output-format`. It rejects cache, profile, model, embedding, NLI,
classification, and offline flags.

The strict extractor recognizes polarity and market structure; numeric operators,
thresholds, currencies, percentages, counts, and bounded buckets; UTC times and
deadlines; allowlisted tournament stages; strict singular-winner templates; and
canonical subject, competition, season, jurisdiction, and resolution signatures.
Ambiguous or unmatched semantic fields remain nullable and never remove a node.

Fast publishes every two-outcome market pair as `complement`, categorical
outcomes as `mutually_exclusive`, plus independently qualified exact equivalence,
numeric containment/disjointness, time containment, stage implication, and
strict single-winner exclusion. Each enabled rule requires 100 positive and 100
adversarial generated cases with no false acceptance.
Cross-market deterministic rules require the same authoritative event scope.
Negated bounded ranges and negated equality are non-convex sets, so fast mode
does not reduce them to unsafe single-interval numeric proofs.

For the WC2026 profile, pipeline semantics take precedence over question text.
The deterministic core publishes same-market complements, same-team progression
implications, reverse negative implications, same-level equivalences, and
pairwise exclusion between different teams' positive winner claims. It never
uses odds timestamps as event intervals or creates cross-team stage
implications.

## Full mode

Full requires `--cache-dir`, `--automation-profile`, both model manifests and
endpoints, and `--compute-profile`. It uses USearch 2.26.0 HNSW with cosine/f32,
connectivity 32, construction/search expansion 128, sorted single-threaded
insertion, 64 retrieved neighbors, exact reranking, and exact top 20 retention.
The embedding model/revision, ANN parameters, insertion order, reranker, NLI,
and inference protocols are profile bindings.

Both Apache-2.0 models must agree on relation and direction and supply valid
source-field citations with empty assumptions. NLI is veto-only. Full stops
scheduling model requests 300 seconds before its monotonic deadline, drains
in-flight work, marks the remainder `deadline_budget_exhausted`, solves, and
publishes. Unassessed pairs are never described as unrelated.

Nodes and authoritative fields always come from deterministic extraction. Full
mode selects at most 256 ambiguous or unmatched markets for dual-model semantic
fallback, prioritizing ambiguous extraction and repeated authoritative event
groups. The selection contract is profile-bound, cached by model role, and must
replay identically offline.

The cache is `<cache-dir>/inference-cache-v8.sqlite3`. Offline replay is
read-only and requires complete role/task coverage. v0.10 caches, profiles,
state, outputs, and release fixtures are incompatible.

## Coverage and provenance

Every output records `build_mode`, `validation_status`, evidence tier, extractor
and rule bindings, source spans, proof scope, assessed/unassessed counts, cutoff,
deadline state, stage wall time, RSS, DuckDB/spill bytes, component sizes, and
publication bytes. Deadline status covers the manifest-complete published graph.
`DETERMINISTIC_VALIDATED` applies only to fast outputs; 0.12 full outputs are
always `EXPERIMENTAL_FULL`. Classification reporting is nullable:
`not_applicable`, `not_started`, `partial`, or `complete`. A zero-eligible fast
run is `not_applicable`, never artificial 100% coverage.
