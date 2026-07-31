# Discovery

Discovery requires two Apache-2.0 manifests and two conforming self-hosted Chat
Completions endpoints. Loopback ports 8080 and 8081 are defaults. Remote
self-hosted inference requires explicit opt-in; credentials, query strings,
fragments, and implicit cloud fallbacks are rejected.

## Catalog selection

The default 5,000-proposition envelope is intended for development. Production
all-market discovery uses `--all-propositions`, selects whole markets in stable
source-hash order, and records the selected market/proposition counts and
truncation status. The canonical release file contains 94,781 rows: 94,777 valid
complete markets, 4 invalid rows excluded with explicit counts, and 189,570
selected propositions.

Candidate generation remains bounded. Deterministic candidates are retained
first. Unresolved classification is scheduled round-robin across canonical
event-pair scopes in stable hashed order to avoid lexical catalog bias, then by
structured evidence and semantic score. The manifest,
`coverage_summary.json`, event/component summaries, and node metrics report
eligible, assessed, unclassified, quarantined, accepted, and rejected counts.
Coverage targets are publication gates, not estimates.

## Consensus and cache

Both models must return every source outcome exactly once. Authoritative IDs,
metadata, polarity, numeric values, units, and dates cannot be overridden.
Semantic fields publish only on normalized agreement. Pair classification uses
the shared atomic relation contract, valid citations, empty assumptions, matching
direction, calibrated confidence, and no NLI veto.

The cache is `<cache-dir>/inference-cache-v7.sqlite3`. Online transient failures
are retried and replaceable; stable successes are immutable. Offline mode is
read-only and reports missing role/task counts. Outputs and state created before
0.10 are incompatible and require clean regeneration.

Incremental baselines must be distinct, manifest-complete 0.10 outputs with exact
model-pair, automation profile, protocol, normalization, retrieval, rule,
explorer-artifact, and state bindings. Threshold-only runs reuse raw predictions.
