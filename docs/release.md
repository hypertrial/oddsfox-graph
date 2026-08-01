# Release validation

Version 0.12 certifies fast mode only. The content-bound release fixture includes
the canonical catalog, a complete fast baseline, expected logical hashes, viewer
and coverage manifests, and a three-repetition M4 performance report. It does
not require model weights, inference cache, or an automation profile.

Each canonical run must read 94,781 rows, reject four invalid rows, select 94,777
markets and 189,570 propositions, publish 94,771 binary complements and 54
same-market categorical exclusions, produce nonzero verified cross-market and
cross-event deterministic edges, meet the 120-second time-to-ready gate, and
produce identical logical hashes. RSS is recorded for diagnosis but is not a
release blocker.

`release-validate --fixture-root ... --work-dir ...` validates fixture paths and
hashes, package/contract versions, catalog counts, fast mode,
`DETERMINISTIC_VALIDATED`, expected artifacts, viewer metadata, and the
performance report. Every reported run is bound to the baseline's expected
logical hashes, and the report must retain the canonical M4 budget metadata and
passing gate results. Outputs with older rules, viewer contracts, or discovery
semantic fingerprints are rejected and must be rebuilt.

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
