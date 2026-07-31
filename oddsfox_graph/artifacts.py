from __future__ import annotations


REPORTS = (
    "summary.md",
    "strongest_implications.md",
    "strongest_exclusions.md",
    "duplicate_edges.md",
    "coverage.md",
    "conditional_examples.md",
)

ARTIFACT_COLUMNS = {
    "nodes.parquet": [
        "node_id",
        "market_id",
        "outcome_index",
        "clob_token_id",
        "question",
        "outcome_label",
        "event_slug",
        "is_active",
        "is_closed",
        "market_family",
        "canonical_proposition",
        "proposition_type",
        "expected_tokens",
        "first_seen_ts",
        "last_seen_ts",
    ],
    "market_groups.parquet": [
        "market_id",
        "event_slug",
        "question",
        "market_family",
        "num_tokens",
        "token_ids",
        "outcome_labels",
        "is_active",
        "is_closed",
        "first_seen_ts",
        "last_seen_ts",
    ],
    "logic_edges.parquet": [
        "src_node_id",
        "dst_node_id",
        "edge_type",
        "edge_basis",
        "confidence",
        "market_id_src",
        "market_id_dst",
        "event_slug_src",
        "event_slug_dst",
        "evidence",
        "discovery_method",
        "rule_version",
        "prompt_version",
        "explanation",
        "assumptions",
        "rule_id",
        "proposal_id",
        "solver_version",
        "constraint_version",
        "solver_component_id",
        "primary_model_version",
        "verifier_model_version",
        "primary_assessment_id",
        "verifier_assessment_id",
        "primary_inference_fingerprint",
        "verifier_inference_fingerprint",
        "consensus_fingerprint",
        "automation_profile_id",
    ],
    "conditional_edges.parquet": [
        "a_node_id",
        "b_node_id",
        "p_a_given_b",
        "method",
        "confidence",
        "evidence",
    ],
}


def reports() -> tuple[str, ...]:
    return tuple(f"reports/{name}" for name in REPORTS)


def artifact_projection(artifact: str) -> str:
    return ", ".join(ARTIFACT_COLUMNS[artifact])
