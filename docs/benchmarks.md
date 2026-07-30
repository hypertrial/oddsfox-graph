# Benchmarks

Use the manifest timing summary after a completed build:

```bash
python -m oddsfox_graph.cli benchmark-summary --out <build-dir>
```

The command prints:

- `runtime_seconds`
- selected count fields from `stats`
- artifact count
- top stage timings from `stage_timings`

For discovery, the summary also reports candidate and review counts. Inspect the
manifest directly for cache, token usage, selected-input counts, requested and
observed models, embedding revision, and every stage timing.

## M4 Smoke

Use the real local catalog and a fresh cache:

```bash
python -m oddsfox_graph.cli discover \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-smoke \
  --cache-dir .cache/oddsfox-graph \
  --max-propositions 2000 \
  --max-candidates 40000 \
  --max-llm-pairs 5000
```

Then rerun the same command with `--offline` and compare
`artifact_hashes` in `build_manifest.json`. The smoke envelope is 500–2,000
selected propositions, at most 40,000 candidates, and at most 5,000 classified
pairs.

After complete human labeling, `review-score` requires deterministic precision
greater than 0.99, overall precision greater than 0.95, candidate recall greater
than 0.95, at least 200 reviewed accepted edges, complete provenance, and no
embedding-only acceptance.
