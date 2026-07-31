# Discovery

Discovery requires two Apache-2.0 model manifests and two conforming self-hosted
Chat Completions endpoints. Loopback ports 8080 and 8081 are defaults. A remote
self-hosted endpoint requires `--allow-remote-inference`; credentials, query
strings, fragments, and implicit cloud fallbacks are rejected.

The parser sends one complete market per request. Both models must return every
source outcome exactly once. Authoritative IDs, source metadata, Yes/No polarity,
numeric operators, thresholds, units, and source dates cannot be overridden.
Normalized semantic fields publish only on exact dual agreement; affected
propositions otherwise remain available for safe same-market facts but are
quarantined from model-derived relations.

Pair classification asks five atomic questions: A entails B, B entails A, both
can be true, at least one must be true, and the propositions are related. Each
model must independently cite exact nonempty supplied fields and return no
assumptions. The lower confidence is compared with the stricter of the generated
qualification threshold and the CLI threshold. NLI may veto but never originate
an edge.

The cache is `<cache-dir>/inference-cache-v7.sqlite3`. Online transient failures
are retried and replaceable; stable results are immutable. Offline mode opens the
database read-only and reports missing role/task counts. Delete the cache directory
to regenerate after a v0.9 protocol or model change.

`--progress-format` sends deterministic stage events to stderr as plain text or
JSON. Final output is independently controlled by `--output-format`.

Incremental baselines must be distinct, manifest-complete v0.9 outputs with exact
model-pair, automation-profile, protocol, normalization, retrieval, rule, and
state bindings. Threshold-only runs reuse raw predictions and rerun acceptance
and solving. Equivalent clean, online-cached, offline, and incremental builds
must have identical logical artifact hashes.
