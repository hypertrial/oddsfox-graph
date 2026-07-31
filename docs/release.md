# Release validation

The installed `release-validate` command validates a content-bound 0.10 fixture.
It includes the canonical 94,781-market input, SQLite cache, bounded 5,000 and
20,000 baselines, a complete-catalog baseline, both model manifests, automation
profile/report/cases, compute profile, expected logical hashes, and performance
report.

Validation checks every file/tree hash, SQLite integrity, model/profile and case
bindings, expected artifact hashes, `AUTOMATION_VALIDATED`, and performance gates.
The complete-catalog baseline must bind all 94,781 input rows, explicitly report
the 4 invalid rows, select 94,777 valid markets and 189,570 propositions, set
`all_market_selection=true`, and include a nonempty viewer graph fingerprint.

Protected validation runs real dual-model qualification, llama.cpp/Metal and
vLLM conformance, clean/online/offline/incremental equality, complete-catalog
discovery, cache integrity, static explorer export, API smoke checks, and browser
tests. It records discovery and query latency, peak RSS, publication and viewer
artifact sizes, token use, energy, and compute cost.

The manual workflow requires an Apple M4 runner with both llama.cpp endpoints and
a Linux GPU runner with both vLLM endpoints. Runtime-bound manifests and model
files are external; the repository never downloads or launches weights. Missing
models, cache coverage, generated cases, or measurements remain explicit release
prerequisites and are never fabricated or waived.
