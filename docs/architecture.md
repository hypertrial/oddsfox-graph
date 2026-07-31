# Architecture

The pipeline has one path:

1. validate and normalize a compact market snapshot;
2. qualify the exact primary/verifier runtime pair on deterministic cases;
3. parse each market independently with both models and merge exact normalized
   agreement, while authoritative source extraction wins conflicts;
4. retrieve bounded structured and blockwise-cosine candidate neighborhoods;
5. score local NLI for prioritization and vetoes;
6. classify unresolved pairs independently with both generative models;
7. quarantine disagreement, assumptions, invalid citations, failures, vetoes,
   and low confidence;
8. solve deterministic and consensus proposals with RC2;
9. publish a DuckDB database, sorted Parquet artifacts, reports, snapshot, state,
   provenance, and the manifest completion marker.

The primary and verifier use the same typed request/response contracts, but each
has its own manifest, endpoint, runtime identity, cache namespace, prompt/schema
fingerprint, and observed-model provenance. A consensus fingerprint binds both
roles and NLI. Candidate reasons affect retrieval order only and are never model
evidence.

SQLite owns inference caching. DuckDB owns candidate, neighborhood, component,
solver, and publication state. Both are transactional; publication stages a new
directory and writes `build_manifest.json` only after validation and the atomic
directory swap.

`prove` traverses implication arcs and both directions of equivalence at query
time. It never traverses complements, exclusions, or compatibility and does not
materialize transitive closure.
