# Discovery

`discover` requires `--mode fast|full`; there is no implicit mode. When
`--max-propositions` is absent, both modes select the complete valid catalog in
stable market order. The canonical release file has 94,781 rows, four rejected
invalid rows, 94,777 selected markets, and 189,570 propositions.
The output must not be the input file or an ancestor directory containing it;
discovery rejects either target before publication.

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
`DETERMINISTIC_VALIDATED` applies only to fast outputs;
v0.11 full outputs are always `EXPERIMENTAL_FULL`.
