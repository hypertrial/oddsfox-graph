# Automated qualification

Fast rules are independently qualified with at least 100 generated positives and
100 adversarial negatives per enabled rule. Generators do not call production
extractor or relation functions. Any failure keeps the rule experimental and its
edges out of fast publication.

Full qualification remains an out-of-band command. It derives cases only from
the canonical catalog, uses no human or model-authored truth, and binds the exact
Qwen/Granite runtimes, v0.11 extractor, normalization, prompts, request/response
schemas, sampling, NLI, MiniLM, USearch version/parameters/insertion order, exact
reranker, and rule registry. The resulting `AUTOMATION_VALIDATED` profile is a
prerequisite to run full mode, but the resulting graph is still labeled
`EXPERIMENTAL_FULL` in this release.

Generated-case metrics certify automated conformance and controlled logical-case
behavior. They are not an independent real-world semantic-accuracy claim. Live
Qwen/Granite quality, sustained thermal behavior, and the one-hour M4 target are
explicitly deferred from the v0.11 fast release gate.
