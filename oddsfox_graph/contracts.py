from __future__ import annotations

from collections.abc import Sequence

from .artifacts import ARTIFACT_COLUMNS
from .queries import DuckDB


INPUT_PRICE_COLUMNS = [
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "market_volume_usd",
    "odds_timestamp",
    "odds_timestamp_epoch",
    "odds_minute_epoch",
    "price",
    "input_canonical_team_name",
    "input_stage_key",
    "input_stage_rank",
    "input_market_direction",
    "input_progression_outcome_label",
    "input_is_progression_token",
    "input_opposite_clob_token_id",
    "input_market_status",
    "input_is_still_alive",
]

TOKEN_STATS_COLUMNS = [
    "node_id",
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "first_seen_ts",
    "last_seen_ts",
    "input_canonical_team_name",
    "input_stage_key",
    "input_stage_rank",
    "input_market_direction",
    "input_progression_outcome_label",
    "input_is_progression_token",
    "input_opposite_clob_token_id",
    "input_market_status",
    "input_is_still_alive",
]

NODES_VIEW_COLUMNS = [
    *TOKEN_STATS_COLUMNS,
    "canonical_proposition",
    "proposition_type",
    "stage_subject",
    "stage_rank",
    "stage_key",
    "canonical_team_name",
    "market_direction",
    "progression_outcome_label",
    "is_progression_node",
    "opposite_clob_token_id",
    "market_status",
    "is_still_alive",
    "is_single_winner_family",
    "expected_tokens",
    "market_family",
]

CANDIDATE_EDGE_COLUMNS = [
    "src_node_id",
    "dst_node_id",
    "candidate_type",
    "candidate_source",
    "candidate_score",
    "market_id_src",
    "market_id_dst",
    "event_slug_src",
    "event_slug_dst",
]

INTERNAL_TABLE_COLUMNS = {
    "input_prices": INPUT_PRICE_COLUMNS,
    "token_minute_prices": INPUT_PRICE_COLUMNS,
    "market_token_counts": ["market_id", "expected_tokens"],
    "token_stats": TOKEN_STATS_COLUMNS,
    "nodes_v": NODES_VIEW_COLUMNS,
    "candidate_edges_v": CANDIDATE_EDGE_COLUMNS,
    "logic_edges_v": ARTIFACT_COLUMNS["logic_edges.parquet"],
    "conditional_edges_v": ARTIFACT_COLUMNS["conditional_edges.parquet"],
}


def sql_column_list(columns: Sequence[str], *, table_alias: str | None = None) -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return ", ".join(f"{prefix}{column}" for column in columns)


def validate_relation_columns(
    db: DuckDB,
    relation: str,
    expected_columns: Sequence[str] | None = None,
) -> None:
    expected = list(expected_columns or INTERNAL_TABLE_COLUMNS[relation])
    actual = [
        str(row["column_name"])
        for row in db.rows(f"DESCRIBE SELECT * FROM {relation}")
    ]
    if actual != expected:
        raise RuntimeError(f"{relation} column contract drift: expected {expected}, got {actual}")
