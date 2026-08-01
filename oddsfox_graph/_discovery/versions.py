"""Independent version identifiers for discovery pipeline compatibility."""

from typing import Final, Literal


CANONICAL_CATALOG_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)

PARSE_PROMPT_VERSION = "consensus-proposition-parse-v3"
CLASSIFY_PROMPT_VERSION = "dual-atomic-relation-v2"
RULE_VERSION = "discovery-rules-v6"
NORMALIZATION_VERSION = "normalization-v5"
DOMAIN_TAXONOMY_VERSION = "domains-v3"
RETRIEVAL_VERSION = "usearch-hnsw-v1"
CANDIDATE_STATE_VERSION = "candidate-components-v8"
EXECUTION_PLAN_VERSION = "execution-plan-v7"
PUBLICATION_VERSION = "discovery-publication-v8"
QUALIFICATION_GENERATOR_VERSION = "catalog-qualification-v2"
QUALIFICATION_CASE_SCHEMA_VERSION = "qualification-cases-v1"
SOURCE_SCHEMA = "polymarket-market-snapshot-v1"
CACHE_ENTRY_VERSION = 8
CACHE_FORMAT = "sqlite-v1"
CACHE_FILENAME = "inference-cache-v8.sqlite3"
MODEL_MANIFEST_SCHEMA_VERSION: Final[Literal["model-manifest-v1"]] = (
    "model-manifest-v1"
)
AUTOMATION_PROFILE_SCHEMA_VERSION: Final[Literal["automation-profile-v2"]] = (
    "automation-profile-v2"
)
INFERENCE_FINGERPRINT_VERSION = "inference-fingerprint-v4"
NLI_INFERENCE_VERSION = "bidirectional-nli-v1"
SOLVER_VERSION = "pysat-rc2-1.9.dev7"
CONSTRAINT_VERSION = "logic-constraints-v1"
DUAL_CONSENSUS_PROTOCOL_VERSION = "dual-consensus-v1"
FAKE_RUNTIME_VERSION = "fake-runtime-v3"
PERFORMANCE_BUDGET_VERSION = "performance-budget-v3"
RELEASE_FIXTURE_SCHEMA_VERSION = "discovery-release-fixture-v7"
VIEWER_API_VERSION = "viewer-api-v2"
VIEWER_ARTIFACT_VERSION = "viewer-artifacts-v2"
VISUALIZATION_LAYOUT_VERSION = "visualization-layout-v2"

EXTRACTOR_ID = "strict-catalog-extractor"
EXTRACTOR_VERSION = "strict-catalog-extractor-v1"
RULE_APPLICABILITY_VERSION = "rule-applicability-v1"
PROOF_SCOPE_VERSION = "proof-scope-v1"
ANN_INDEX_VERSION = "usearch-2.26.0-hnsw-cos-f32-c32-e128"
PARSE_FALLBACK_VERSION = "structured-candidate-value-v1"
