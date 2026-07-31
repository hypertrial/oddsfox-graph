"""Independent version identifiers for discovery pipeline compatibility."""

from typing import Final, Literal


PARSE_PROMPT_VERSION = "proposition-parse-v3"
CLASSIFY_PROMPT_VERSION = "atomic-relation-v2"
RULE_VERSION = "discovery-rules-v4"
NORMALIZATION_VERSION = "normalization-v3"
DOMAIN_TAXONOMY_VERSION = "domains-v2"
RETRIEVAL_VERSION = "blockwise-cosine-v4"
CANDIDATE_STATE_VERSION = "candidate-components-v5"
EXECUTION_PLAN_VERSION = "execution-plan-v4"
PUBLICATION_VERSION = "discovery-publication-v5"
BENCHMARK_VERSION = "v0.8.0"
BENCHMARK_SCHEMA_VERSION = "benchmark-v2"
SOURCE_SCHEMA = "polymarket-market-snapshot-v1"
CACHE_ENTRY_VERSION = 6
CACHE_FORMAT = "sqlite-v1"
CACHE_FILENAME = "inference-cache-v6.sqlite3"
MODEL_MANIFEST_SCHEMA_VERSION: Final[Literal["model-manifest-v1"]] = (
    "model-manifest-v1"
)
MODEL_PROFILE_SCHEMA_VERSION: Final[Literal["model-profile-v2"]] = (
    "model-profile-v2"
)
INFERENCE_FINGERPRINT_VERSION = "inference-fingerprint-v2"
NLI_INFERENCE_VERSION = "bidirectional-nli-v1"
SOLVER_VERSION = "pysat-rc2-1.9.dev7"
CONSTRAINT_VERSION = "logic-constraints-v1"
FAKE_RUNTIME_VERSION = "fake-runtime-v2"
PERFORMANCE_BUDGET_VERSION = "performance-budget-v1"
RELEASE_FIXTURE_SCHEMA_VERSION = "discovery-release-fixture-v4"
