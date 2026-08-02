# Performance

The v0.13 fast performance contract keeps the large generic catalog as a
scalability fixture while the supported product explorer remains WC2026-only.
The single packaged budget is bound to the canonical input hash and profile,
an exact macOS arm64 `Apple M4` processor identity, Python 3.11,
complete-catalog selection, semantic and source-tree fingerprints, the SHA-256
of the benchmark harness, current publication/viewer contracts, and three
process-isolated repetitions. `Apple M4 Pro` and `Apple M4 Max` hosts are not
equivalent release-gate machines.

`fast-ready-benchmark-v2` measures from discovery-process start through a
durable build manifest and a successful metadata query performed by a newly
started `python -I` interpreter. Interpreter startup, cold imports,
`Graph.open()`, the metadata query, serialization, and probe-process completion
are all inside the measured interval. It does not launch the WC2026-only web
application for a generic graph. Every repetition must be manifest-and-query
ready within 120 seconds and preserve identical logical artifact hashes.

Each report writes the benchmark harness SHA-256 and embeds the same digest in
its validated budget version bindings. Editing or replacing the probe requires
a new bound digest; reports from the previous in-process warm probe cannot be
accepted as current evidence.

The budget is packaged at
`oddsfox_graph/benchmarks/m4-v0.13-fast-performance-budget.json`. `doctor
--mode fast` validates its contents. It reports `not_applicable`, rather than a
false pass, when the valid M4 generic-catalog budget does not describe the
current input, exact processor, Python runtime, or other bound environment.

Measurements include per-stage wall time, RSS high water, DuckDB and spill bytes,
candidate/proof counts, component sizes, isolated nodes, publication bytes, and
time to the first successful metadata query. Peak RSS is diagnostic under the
current release policy, not a blocking threshold. WC2026 discovery must not add
layout, browser, or recording work to this measured path.

Full mode targets under one hour only when both endpoints are already warm and
an exact profile and embeddings are present. Model downloads and qualification
are excluded. v0.13 records deadline/cutoff and throughput data but intentionally
does not certify this live target.
