# Release validation

Version 0.13 certifies fast mode only. The content-bound release fixture includes
the canonical catalog, a complete fast baseline, expected logical hashes, viewer
and coverage manifests, and a three-repetition M4 performance report. It does
not require model weights, inference cache, or an automation profile.

Each canonical run must read 94,781 rows, reject four invalid rows, select 94,777
markets and 189,570 propositions, publish 94,771 binary complements and 54
same-market categorical exclusions, produce nonzero verified cross-market
deterministic edges while refusing unsupported cross-event inference, meet the
120-second manifest-and-query-ready gate, and produce identical logical hashes.
RSS is recorded for diagnosis but is not a release blocker.

`release-validate --fixture-root ... --work-dir ...` validates fixture paths and
hashes, package/contract versions, catalog counts, fast mode,
`DETERMINISTIC_VALIDATED`, expected artifacts, viewer metadata, and the
performance report. Every reported run is bound to the baseline's expected
logical hashes, and the report must exactly match the packaged v4 M4 budget,
including semantic/source-tree fingerprints and the v2 readiness metric.
Outputs with older build, viewer, or discovery semantic contracts are rejected
and must be rebuilt.

## Produce and consume the release fixture

The fixture is produced by `.github/workflows/manual-release-fixture.yml` on the
exact self-hosted M4 runner. The canonical catalog must already exist outside
the Actions checkout so checkout cleanup cannot remove it. Dispatch **Manual
v0.13 Release Fixture** with that absolute path. The workflow verifies the fixed
catalog SHA-256, runs all three isolated performance repetitions, uses the third
identical run as the fast baseline, assembles the fixture, validates it with the
installed release validator, and uploads the 14-day artifact named
`discovery-release-fixture`.

The same assembly step is available locally:

```bash
python scripts/assemble_release_fixture.py \
  --input /absolute/path/to/canonical-catalog.parquet \
  --fast-baseline output/performance-runs/fast-run-3 \
  --performance-report output/performance_report.json \
  --destination output/discovery-release-fixture
```

The destination must not exist or overlap an input. Assembly copies only
regular files, rejects symlinks, derives `expected_artifact_hashes.json` from
the strict fast build manifest, binds every required file plus the complete
baseline tree, and runs `release-validate` against staging. Only a validated
fixture is atomically renamed into place; interruption or failure removes
staging and leaves existing paths untouched.

After the producer succeeds, dispatch **Manual v0.13 Release Validation** with
the producer run ID and the canonical input SHA-256
`790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2`.
That consumer downloads the named artifact, validates it from the built wheel,
and independently repeats the M4 benchmark against the same input.

Release CI also enforces delivery budgets: the built wheel must be at most 2
MiB, the browser entry chunk at most 70 KiB Brotli, and a static explorer's
combined core and graph data at most 1.25 MiB raw and 200 KiB gzip. Static
exports fail atomically with guidance to choose a narrower scope when either
data limit is exceeded.
Build release wheels with `python -m build` so the wheel is produced from the
fresh source distribution; a direct in-place `build --wheel` can reuse an old
ignored `build/` tree. The wheel checker also opens the archive, verifies the
current budget and explorer entry, and rejects stale DuckDB-Wasm assets or
duplicate entry chunks.

The WC2026 adapter has a separate synthetic contract fixture covering the exact
pipeline column/grain/orientation rules and deterministic team-stage graph. A
manual release smoke uses an operator-local real pipeline export, verifies the
human explorer and standalone HTML, and never commits the Parquet or completed
graph. The product describes this universe as team-progression markets, not a
complete WC2026 catalog.

Full mode is implemented and network-free tested, but live dual-model
qualification, semantic gates, sustained thermal runs, and a one-hour acceptance
run are future prerequisites. Missing full-mode evidence does not block the fast
release and must never be represented as completed validation.
