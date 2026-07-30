from __future__ import annotations


PARQUET_ARTIFACTS = (
    "nodes.parquet",
    "market_groups.parquet",
    "logic_edges.parquet",
    "conditional_edges.parquet",
)

REPORTS = (
    "summary.md",
    "strongest_implications.md",
    "strongest_exclusions.md",
    "duplicate_edges.md",
    "coverage.md",
    "conditional_examples.md",
)

FINAL_EDGE_ARTIFACT_TABLES = {
    "logic_edges.parquet": "logic_edges_v",
    "conditional_edges.parquet": "conditional_edges_v",
}

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


def parquet_artifacts() -> tuple[str, ...]:
    return PARQUET_ARTIFACTS


def reports() -> tuple[str, ...]:
    return tuple(f"reports/{name}" for name in REPORTS)


def artifact_projection(artifact: str, *, table_alias: str | None = None) -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return ", ".join(f"{prefix}{column}" for column in ARTIFACT_COLUMNS[artifact])
