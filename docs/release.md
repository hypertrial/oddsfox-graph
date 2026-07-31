# Release validation

The installed `release-validate` command validates a content-bound v0.9 fixture.
The fixture contains the canonical 94,781-market input, SQLite cache, 5k and 20k
manifest-complete baselines, both model manifests, automation profile/report/cases,
compute profile, expected logical hashes, and performance report.
Validation checks every file/tree hash, SQLite integrity, canonical manifest and
profile IDs, generated-case-set binding, baseline profile/version bindings, expected
artifact hashes, and the recorded performance decision.

Protected validation runs real dual-model qualification, llama.cpp/Metal and vLLM
conformance, online/offline/incremental equivalence, cache integrity, and the
non-model M4 budgets. Live model throughput, tokens, energy, cost, RSS, and wall
time are measured, but v0.9 sets no hard live-model wall-time ceiling.

The non-model performance benchmark creates and validates the exact automated
qualification profile once before its timed repetitions. Each clean repetition
starts with a copy containing only that profile and its qualification inference;
production parsing, retrieval, consensus classification, solving, and publication
remain uncached and timed. Qualification runtime and live model throughput are
measured separately by the protected runtime jobs.

The manual workflow requires an `oddsfox-m4` Apple Silicon runner with both
llama.cpp endpoints already running and an `oddsfox-vllm` Linux GPU runner with
both vLLM endpoints running. Runtime-bound manifests and model files are external
release inputs; the workflow never downloads or launches weights.

Release succeeds only with `AUTOMATION_VALIDATED`. Missing models, model files,
cache coverage, generated cases, or measured performance remain explicit external
prerequisites and are never fabricated or waived.
