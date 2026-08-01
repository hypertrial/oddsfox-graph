"""Independent version identifiers for discovery pipeline compatibility."""

from typing import Final, Literal


CANONICAL_CATALOG_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)

PARSE_PROMPT_VERSION = "consensus-proposition-parse-v2"
CLASSIFY_PROMPT_VERSION = "dual-atomic-relation-v1"
RULE_VERSION = "discovery-rules-v5"
NORMALIZATION_VERSION = "normalization-v4"
DOMAIN_TAXONOMY_VERSION = "domains-v3"
RETRIEVAL_VERSION = "blockwise-cosine-v5"
CANDIDATE_STATE_VERSION = "candidate-components-v7"
EXECUTION_PLAN_VERSION = "execution-plan-v6"
PUBLICATION_VERSION = "discovery-publication-v7"
QUALIFICATION_GENERATOR_VERSION = "catalog-qualification-v1"
QUALIFICATION_CASE_SCHEMA_VERSION = "qualification-cases-v1"
SOURCE_SCHEMA = "polymarket-market-snapshot-v1"
CACHE_ENTRY_VERSION = 7
CACHE_FORMAT = "sqlite-v1"
CACHE_FILENAME = "inference-cache-v7.sqlite3"
MODEL_MANIFEST_SCHEMA_VERSION: Final[Literal["model-manifest-v1"]] = (
    "model-manifest-v1"
)
AUTOMATION_PROFILE_SCHEMA_VERSION: Final[Literal["automation-profile-v1"]] = (
    "automation-profile-v1"
)
INFERENCE_FINGERPRINT_VERSION = "inference-fingerprint-v3"
NLI_INFERENCE_VERSION = "bidirectional-nli-v1"
SOLVER_VERSION = "pysat-rc2-1.9.dev7"
CONSTRAINT_VERSION = "logic-constraints-v1"
DUAL_CONSENSUS_PROTOCOL_VERSION = "dual-consensus-v1"
FAKE_RUNTIME_VERSION = "fake-runtime-v3"
PERFORMANCE_BUDGET_VERSION = "performance-budget-v2"
RELEASE_FIXTURE_SCHEMA_VERSION = "discovery-release-fixture-v6"
VIEWER_API_VERSION = "viewer-api-v1"
VIEWER_ARTIFACT_VERSION = "viewer-artifacts-v1"
VISUALIZATION_LAYOUT_VERSION = "visualization-layout-v1"
