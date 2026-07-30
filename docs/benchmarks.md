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

## v0.4 Human Benchmark

The benchmark is generated only from the supplied catalog with SHA-256
`790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2`.
All pair endpoints come from the top-5,000 discovery population so the same
truth set can score both release sizes.

```bash
python -m oddsfox_graph.cli benchmark-export \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-5000 \
  --output-dir output/benchmark-review \
  --parse-count 500 \
  --pair-count 2000 \
  --seed 20260730
```

The export contains blinded reviewer-A and reviewer-B files plus an adjudication
template. It stratifies parse records across sports, elections,
cryptocurrency, economic indicators, and date-based events. Pair records mix
candidate, semantic near-miss, structured near-miss, and noncandidate sources.
No model label, method, or confidence appears in reviewer evidence. The
sampling manifest records domain and pair-source counts without exposing them
as reviewer labels.

Both reviewers must use distinct aliases and supply notes. Every disagreement
must receive a final adjudicated label and notes before compilation:

```bash
python -m oddsfox_graph.cli benchmark-compile \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --review-a output/benchmark-review/reviewer-a.csv \
  --review-b output/benchmark-review/reviewer-b.csv \
  --adjudication output/benchmark-review/adjudication.csv \
  --output oddsfox_graph/benchmarks/v0.4.0.parquet
```

Do not synthesize or copy model predictions into the human-label columns.

## M4 Release Runs

Use the real local catalog and a fresh cache:

```bash
python -m oddsfox_graph.cli discover \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery-5000 \
  --cache-dir .cache/oddsfox-graph \
  --max-propositions 5000 \
  --max-candidates 400000 \
  --max-llm-pairs 5000
```

Repeat at 20,000 propositions. Then rerun each completed output with `--offline`
and compare `artifact_hashes` in `build_manifest.json`. Validate incremental
changes against a distinct immutable baseline and compare each result to a clean
full build using the same configuration.

Evaluation with a versioned token-pricing snapshot is:

```bash
python -m oddsfox_graph.cli evaluate \
  --out output/discovery-20000 \
  --benchmark oddsfox_graph/benchmarks/v0.4.0.parquet \
  --pricing-file pricing.json
```

`READY_TO_SCALE` requires deterministic precision at least 0.99, overall
precision at least 0.97, candidate recall at least 0.98, complement precision
at least 0.995, unsupported-assumption rate below 0.01, complete accepted-edge
provenance, deterministic replay, affected-component-only recomputation, and
successful local M4 completion.

The protected release fixture can validate both limits and completed labels in
one command:

```bash
python scripts/validate_discovery_release.py \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --cache-dir <complete-cache> \
  --baseline-dir <completed-online-baselines> \
  --work-dir output/release-validation \
  --expected-hashes <expected-artifact-hashes.json> \
  --benchmark <compiled-benchmark.parquet> \
  --pricing-file <pricing.json>
```

## Historical v0.3.1 Performance Reference

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

The legacy `review-score` thresholds remain available only for v0.3
compatibility. v0.4 release decisions come from `evaluation_report.json`.
