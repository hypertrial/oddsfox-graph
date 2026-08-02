# Automated qualification

Fast rules are independently qualified with at least 100 generated positives and
100 adversarial negatives per enabled rule. Generators do not call production
extractor or relation functions. Any failure keeps the rule experimental and its
edges out of fast publication.

Full qualification remains an out-of-band command. Always pass the same explicit
input profile used for discovery. The WC2026 profile validates and parses every
collapsed progression market, then generates 5,000 controlled pair cases with a
deterministic, market-disjoint 60/40 selection/validation split. Its case schema
and generator versions are independently compatibility-bound. The generic
catalog profile retains its five-domain 1,000-parse/5,000-pair contract.

Qualification derives cases only from the selected canonical input, uses no
human or model-authored truth, and binds the exact
Qwen/Granite runtimes, v0.13 extractor, normalization, prompts, request/response
schemas, sampling, NLI, MiniLM, USearch version/parameters/insertion order, exact
reranker, and rule registry. The resulting `AUTOMATION_VALIDATED` profile is a
prerequisite to run full mode, but the resulting graph is still labeled
`EXPERIMENTAL_FULL` in this release.

```bash
oddsfox-graph qualify \
  --input-profile polymarket-wc2026-graph-hourly-v1 \
  --input "$ODDSFOX_DATA_DIR/exports/wc2026_graph_hourly.parquet" \
  --out qualification --cache-dir cache \
  --primary-model-manifest primary.json --verifier-model-manifest verifier.json \
  --compute-profile compute.json
```

Generated-case metrics certify automated conformance and controlled logical-case
behavior. They are not an independent real-world semantic-accuracy claim. Live
Qwen/Granite quality, sustained thermal behavior, and the one-hour M4 target are
explicitly deferred from the v0.13 fast release gate.
