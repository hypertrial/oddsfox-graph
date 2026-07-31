# Architecture

`discover` loads `polymarket-market-snapshot-v1`, selects complete markets,
parses one market per constrained local-model request, normalizes propositions,
retrieves bounded candidate pairs, applies benchmark-enabled deterministic
rules, scores NLI in both directions, and classifies unresolved pairs with
atomic judgments.

Every proposal enters independent connected-component RC2 solving. Same-market
facts are hard constraints; calibrated rule, NLI, and generative proposals are
weighted soft clauses. Publication validates canonical direction, incompatible
relations, provenance, state fingerprints, schemas, and artifact counts before
atomically replacing the output. `build_manifest.json` is written last.

Inference fingerprints bind weights, runtime family/version, prompt, typed
request and response schemas, sampling settings, and output limits. Discovery,
model conformance, and calibration share the same canonical request builders.
Candidate reasons stay outside model evidence. Cache entries use fingerprinted
keys in a transactional SQLite database. Incremental invalidation tracks
market, parse, normalization, embedding, candidate, classification, threshold,
and solver-component changes under `state/`.

Candidate, embedding, semantic-neighbor, and component state stays in the
staged DuckDB workspace. Bounded rows are streamed into inference, and the
workspace is promoted directly into the final graph database rather than
copied during publication.

`discovery_method` is `deterministic`, `generative_model`, or `nli`. Embedding
similarity only retrieves candidates and never publishes an edge.

The sibling `oddsfox-pipeline` integration must be migrated to consume a
manifest-complete `discover` output; this repository provides no alternate
publication command.
