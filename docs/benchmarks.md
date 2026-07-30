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

The protected release fixture can validate both limits and completed labels in
one command:

```bash
python scripts/validate_discovery_release.py \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --cache-dir <complete-cache> \
  --work-dir output/release-validation \
  --expected-hashes <expected-artifact-hashes.json> \
  --labels <completed-labels.csv>
```

## v0.3.1 Performance Reference

On the July 30, 2026 development machine, using the supplied 94,781-market
parquet and deterministic fake model/embedding responses:

| Measurement | v0.3.0 | v0.3.1 |
|---|---:|---:|
| 2,000-proposition total runtime | 14.769 s | 2.482 s |
| artifact publication stage | 13.114 s | 1.387 s |
| 10,000-row typed insert median | 1.798 s | 0.039 s |

The v0.3.1 real-data run selected 1,000 complete markets, retained the configured
40,000 candidates, classified 200 fake-response pairs, and reproduced all
logical artifact hashes offline. The v0.3.1 discovery figures are medians from
three fresh-cache runs. Treat them as a machine-specific reference, not a CI
wall-clock assertion.

After complete human labeling, `review-score` requires deterministic precision
greater than 0.99, overall precision greater than 0.95, candidate recall greater
than 0.95, at least 200 reviewed accepted edges, complete provenance, and no
embedding-only acceptance.
