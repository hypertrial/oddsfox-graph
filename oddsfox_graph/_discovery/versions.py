"""Independent version identifiers for discovery pipeline compatibility."""

import hashlib
import json
from pathlib import Path
from typing import Final, Literal


CANONICAL_CATALOG_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)

PARSE_PROMPT_VERSION = "consensus-proposition-parse-v3"
CLASSIFY_PROMPT_VERSION = "dual-atomic-relation-v2"
RULE_VERSION = "discovery-rules-v7"
NORMALIZATION_VERSION = "normalization-v6"
DOMAIN_TAXONOMY_VERSION = "domains-v3"
RETRIEVAL_VERSION = "usearch-hnsw-v1"
CANDIDATE_STATE_VERSION = "candidate-components-v9"
EXECUTION_PLAN_VERSION = "execution-plan-v8"
PUBLICATION_VERSION = "discovery-publication-v10"
QUALIFICATION_GENERATOR_VERSION = "catalog-qualification-v2"
QUALIFICATION_CASE_SCHEMA_VERSION = "qualification-cases-v1"
WC2026_QUALIFICATION_GENERATOR_VERSION = "wc2026-catalog-qualification-v1"
WC2026_QUALIFICATION_CASE_SCHEMA_VERSION = "wc2026-qualification-cases-v1"
SOURCE_SCHEMA = "polymarket-market-snapshot-v1"
WC2026_SOURCE_SCHEMA = "polymarket-wc2026-graph-hourly-v1"
INPUT_ADAPTER_VERSION = "input-adapters-v2"
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
BUILD_MANIFEST_SCHEMA_VERSION = "graph-build-manifest-v1"
PERFORMANCE_BUDGET_VERSION = "performance-budget-v4"
FAST_READY_BENCHMARK_VERSION = "fast-ready-benchmark-v2"
FAST_READY_BENCHMARK_HARNESS_SHA256 = (
    "bab11484d6ee2501af4bea226982b0dd45b8b39273a7c6dee1c67d3adf912921"
)
RELEASE_FIXTURE_SCHEMA_VERSION = "discovery-release-fixture-v8"
VIEWER_API_VERSION = "viewer-api-v4"
VIEWER_ARTIFACT_VERSION = "viewer-artifacts-v4"
STATIC_EXPLORER_VERSION = "static-explorer-v5"
STATIC_EXPLORER_CORE_VERSION = "static-explorer-core-v1"
STATIC_EXPLORER_GRAPH_VERSION = "static-explorer-graph-v1"
VISUALIZATION_LAYOUT_VERSION = "visualization-layout-v2"
AGGREGATION_CONTRACT_VERSION = "explorer-aggregation-v3"
COVERAGE_SUMMARY_VERSION = "coverage-summary-v2"
EXPLORER_DERIVED_SEMANTICS_VERSION = "explorer-derived-semantics-v1"
ESSENTIAL_PROJECTION_VERSION = "essential-projection-v2"

EXTRACTOR_ID = "strict-catalog-extractor"
EXTRACTOR_VERSION = "strict-catalog-extractor-v2"
RULE_APPLICABILITY_VERSION = "rule-applicability-v2"
PROOF_SCOPE_VERSION = "proof-scope-v2"
ANN_INDEX_VERSION = "usearch-2.26.0-hnsw-cos-f32-c32-e128"
PARSE_FALLBACK_VERSION = "structured-candidate-value-v1"


def ann_version_binding() -> dict[str, str | int]:
    """Return the single manifest representation of the ANN implementation."""

    return {
        "implementation": "usearch",
        "version": "2.26.0",
        "metric": "cos",
        "dtype": "f32",
        "connectivity": 32,
        "construction_expansion": 128,
        "search_expansion": 128,
        "query_neighbors": 64,
        "exact_reranking": "exact-cosine-score-id-v1",
        "construction_threads": 1,
    }


def rule_registry_hash() -> str:
    """Return the hash written into full-mode manifest version bindings."""

    from .provenance import text_sha256
    from .relations import RULE_REGISTRY

    return text_sha256(json.dumps(RULE_REGISTRY, sort_keys=True))


def source_tree_fingerprint() -> str:
    """Hash installed first-party discovery sources for audit, not compatibility."""

    package_root = Path(__file__).resolve().parents[1]
    paths = sorted((package_root / "_discovery").glob("*.py"))
    paths.extend(
        path
        for path in (package_root / "discovery.py", package_root / "qualification.py")
        if path.is_file()
    )
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discovery_semantics_fingerprint() -> str:
    """Hash contracts that can change the meaning of a published graph."""

    from .artifact_contracts import PROPOSITION_COLUMNS
    from .provenance import canonical_json_sha256
    from .relations import RULE_REGISTRY
    from .._explorer.aggregation import (
        COMPONENT_SUMMARY_COLUMNS,
        EVENT_RELATION_SUMMARY_COLUMNS,
        EVENT_SUMMARY_COLUMNS,
        NODE_METRIC_COLUMNS,
        VISUALIZATION_LAYOUT_COLUMNS,
    )

    return canonical_json_sha256(
        {
            "version_registry": {
                "input_adapter": INPUT_ADAPTER_VERSION,
                "normalization": NORMALIZATION_VERSION,
                "extractor": EXTRACTOR_VERSION,
                "extractor_id": EXTRACTOR_ID,
                "proof_scope": PROOF_SCOPE_VERSION,
                "rule_applicability": RULE_APPLICABILITY_VERSION,
                "rules": RULE_VERSION,
                "domain_taxonomy": DOMAIN_TAXONOMY_VERSION,
                "retrieval": RETRIEVAL_VERSION,
                "parse_fallback": PARSE_FALLBACK_VERSION,
                "ann_index": ANN_INDEX_VERSION,
                "ann_binding": ann_version_binding(),
                "parse_prompt": PARSE_PROMPT_VERSION,
                "classify_prompt": CLASSIFY_PROMPT_VERSION,
                "cache_entry": CACHE_ENTRY_VERSION,
                "cache_format": CACHE_FORMAT,
                "inference_fingerprint": INFERENCE_FINGERPRINT_VERSION,
                "nli_inference": NLI_INFERENCE_VERSION,
                "dual_consensus": DUAL_CONSENSUS_PROTOCOL_VERSION,
                "solver": SOLVER_VERSION,
                "constraints": CONSTRAINT_VERSION,
                "candidate_state": CANDIDATE_STATE_VERSION,
                "execution_plan": EXECUTION_PLAN_VERSION,
                "publication": PUBLICATION_VERSION,
                "viewer_api": VIEWER_API_VERSION,
                "viewer_artifacts": VIEWER_ARTIFACT_VERSION,
                "visualization_layout": VISUALIZATION_LAYOUT_VERSION,
                "aggregation": AGGREGATION_CONTRACT_VERSION,
                "coverage_summary": COVERAGE_SUMMARY_VERSION,
                "explorer_derived_semantics": (
                    EXPLORER_DERIVED_SEMANTICS_VERSION
                ),
                "essential_projection": ESSENTIAL_PROJECTION_VERSION,
                "static_explorer_core": STATIC_EXPLORER_CORE_VERSION,
                "static_explorer_graph": STATIC_EXPLORER_GRAPH_VERSION,
                "source_schemas": (SOURCE_SCHEMA, WC2026_SOURCE_SCHEMA),
                "qualification_generators": (
                    QUALIFICATION_GENERATOR_VERSION,
                    WC2026_QUALIFICATION_GENERATOR_VERSION,
                ),
                "qualification_case_schemas": (
                    QUALIFICATION_CASE_SCHEMA_VERSION,
                    WC2026_QUALIFICATION_CASE_SCHEMA_VERSION,
                ),
            },
            "rule_registry": RULE_REGISTRY,
            "proposition_columns": PROPOSITION_COLUMNS,
            "aggregation_columns": {
                "event_summary": EVENT_SUMMARY_COLUMNS,
                "event_relation_summary": EVENT_RELATION_SUMMARY_COLUMNS,
                "component_summary": COMPONENT_SUMMARY_COLUMNS,
                "node_metrics": NODE_METRIC_COLUMNS,
                "visualization_layout": VISUALIZATION_LAYOUT_COLUMNS,
            },
        }
    )
