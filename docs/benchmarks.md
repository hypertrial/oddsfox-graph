# Benchmark and Release Gates

The canonical source is the supplied 94,781-market catalog with SHA-256
`790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2`.

Export blinded reviewer material:

```bash
oddsfox-graph benchmark-export \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --out output/discovery \
  --output-dir reviews \
  --parse-count 750 \
  --pair-count 3000 \
  --seed 0
```

After two independent reviewers and adjudication:

```bash
oddsfox-graph benchmark-compile \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --review-a reviews/reviewer-a.csv \
  --review-b reviews/reviewer-b.csv \
  --adjudication reviews/adjudication.csv \
  --sampling-manifest reviews/sampling_manifest.json \
  --output oddsfox_graph/benchmarks/v0.8.0.parquet

oddsfox-graph model-profile \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --benchmark oddsfox_graph/benchmarks/v0.8.0.parquet \
  --cache-dir output/cache \
  --model-manifest model-manifest.json \
  --out output/profile

oddsfox-graph evaluate \
  --out output/discovery \
  --benchmark oddsfox_graph/benchmarks/v0.8.0.parquet \
  --compute-profile compute-profile.json
```

Development rows support prompt and rule work, calibration rows build profiles,
and untouched test rows determine `READY_TO_SCALE`. Release gates require
deterministic precision at least 0.99, overall precision at least 0.97,
candidate recall at least 0.98, complement precision at least 0.995,
unsupported-assumption rate below 0.01, complete provenance, deterministic
offline replay, affected-only incremental recomputation, an approved local
runtime and license, exact model-profile matching, at least 0.999 structured
validity, and successful 5,000/20,000-proposition runs.

`scripts/benchmark_discovery.py` measures absolute runtime, stage-level peak
RSS, cache/workspace/publication sizes, candidate/publication timings, offline
reuse, and one-market incremental reuse against the canonical catalog. The
release fixture uses schema `discovery-release-fixture-v4`.

## Release procedure

Run current real-catalog performance and reuse validation:

```bash
python scripts/benchmark_discovery.py \
  --input data/polymarket_all_markets_20260730T093857Z.parquet \
  --output fixture/performance-report.json \
  --sizes 5000,20000 \
  --modes clean,offline,one-market-incremental \
  --repetitions 3 \
  --performance-budget benchmarks/m4-v0.8-performance-budget.json \
  --require-gates
```

The checked-in performance budget is bound to the canonical catalog,
`fake-runtime-v2`, Darwin arm64 Apple M4, and three repetitions. It requires a
5,000-proposition clean median of at most 10.32 seconds, a
20,000-proposition peak RSS median of at most 1,688 MB, and a 20,000
publication median of at most 3.67 seconds, while retaining the offline and
incremental reuse gates. It is not a portable live-model throughput guarantee.

After live llama.cpp/Metal and vLLM conformance runs, genuine benchmark
compilation, calibration, and a `READY_TO_SCALE` test evaluation, assemble the
content-bound fixture. It must contain the canonical input, cache, 5,000 and
20,000 baselines, expected hashes, benchmark, compute profile, model
manifest/profile, calibration report, and the passing three-repetition M4
performance report:

Release validation requires the exact checked-in budget and recomputes every
gate from the raw per-run measurements; stored summaries and pass flags are not
accepted as independent evidence.

```bash
python scripts/create_release_fixture_manifest.py \
  --fixture-root fixture

python scripts/validate_discovery_release.py \
  --input fixture/input.parquet \
  --cache-dir fixture/cache \
  --baseline-dir fixture/baselines \
  --work-dir output/release-validation \
  --expected-hashes fixture/expected-artifact-hashes.json \
  --benchmark fixture/benchmark.parquet \
  --performance-report fixture/performance-report.json \
  --compute-profile fixture/compute-profile.json \
  --model-manifest fixture/model-manifest.json \
  --model-profile fixture/model-profile.json \
  --calibration-report fixture/calibration-report.json \
  --fixture-manifest fixture/fixture-manifest.json
```

The sibling `oddsfox-pipeline` must switch to consuming a manifest-complete
discovery output before this breaking release is integrated; no command shim is
provided here.
