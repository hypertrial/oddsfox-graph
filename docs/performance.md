# Performance

The v0.12 fast performance contract preserves the measured v0.11 canonical-catalog
budget while adding the smaller WC2026 input profile. The packaged budget is bound to
the canonical input hash, macOS
arm64 Apple M4, complete-catalog selection, extractor/rule versions, and three
process-isolated repetitions. Every repetition must make `/api/v1/meta` ready
within 120 seconds and preserve identical logical artifact hashes.
The same budget is packaged with the Python distribution so `doctor --mode
fast` validates the installed contract rather than depending on a source tree.

Measurements include per-stage wall time, RSS high water, DuckDB and spill bytes,
candidate/proof counts, component sizes, isolated nodes, publication bytes, and
time to the first successful metadata response. Peak RSS is diagnostic under the
current release policy, not a blocking threshold. WC2026 discovery must not add
layout, browser, or recording work to this measured path.

Full mode targets under one hour only when both endpoints are already warm and
an exact profile and embeddings are present. Model downloads and qualification
are excluded. v0.12 records deadline/cutoff and throughput data but intentionally
does not certify this live target.
