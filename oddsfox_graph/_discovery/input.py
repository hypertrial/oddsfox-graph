from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from .contracts import InputProfile, SourceMarket, SourceOutcome
from .provenance import canonical_json_sha256
from .versions import INPUT_ADAPTER_VERSION, SOURCE_SCHEMA, WC2026_SOURCE_SCHEMA
from ..queries import DuckDB, q


def load_source_markets(
    input_path: Path,
    *,
    max_propositions: int | None = None,
    input_profile: InputProfile = "auto",
) -> tuple[str, int, list[SourceMarket], dict[str, object]]:
    profile = resolve_input_profile(input_path, input_profile)
    if profile == WC2026_SOURCE_SCHEMA:
        if max_propositions is not None:
            raise ValueError(
                "max_propositions is not supported for the WC2026 graph profile; "
                "partial team progression chains are unsafe"
            )
        return _load_wc2026_markets(input_path)

    db = DuckDB()
    try:
        db.execute("SET TimeZone = 'UTC'")
        schema = {
            str(row["column_name"]).lower(): str(row["column_type"]).upper()
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(input_path)}')"
            )
        }
        columns = set(schema)
        input_rows = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(input_path)}')") or 0
        )
        required = {"market_id", "question", "outcomes", "clob_token_ids"}
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                "Discovery input must use the polymarket-market-snapshot-v1 "
                "schema; missing columns: " + ", ".join(missing)
            )
        expected_types = {
            "market_id": "VARCHAR",
            "question": "VARCHAR",
            "outcomes": "VARCHAR[]",
            "clob_token_ids": "VARCHAR[]",
        }
        wrong_types = {
            name: schema[name]
            for name, expected in expected_types.items()
            if schema[name] != expected
        }
        if wrong_types:
            details = ", ".join(
                f"{name}={actual} (expected {expected_types[name]})"
                for name, actual in sorted(wrong_types.items())
            )
            raise ValueError(
                "Discovery input must use the polymarket-market-snapshot-v1 "
                f"schema; incompatible column types: {details}"
            )
        if "tags" in schema and schema["tags"] != "VARCHAR[]":
            raise ValueError(
                "Discovery input must use the polymarket-market-snapshot-v1 "
                "schema; tags must have type VARCHAR[]"
            )
        invalid_market_rows = int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(input_path)}')
                WHERE market_id IS NULL
                   OR question IS NULL
                   OR outcomes IS NULL
                   OR clob_token_ids IS NULL
                   OR len(outcomes) = 0
                   OR len(outcomes) != len(clob_token_ids)
                """
            )
            or 0
        )
        input_propositions = int(
            db.scalar(
                f"""
                SELECT sum(coalesce(len(outcomes), 0))
                FROM read_parquet('{q(input_path)}')
                """
            )
            or 0
        )
        _validate_compact_catalog(db, input_path)
        eligible_summaries = _compact_market_summaries(
            db,
            input_path,
            columns,
        )
        selected_ids = (
            _select_market_summaries(eligible_summaries, max_propositions)
            if max_propositions is not None
            else [str(row["market_id"]) for row in eligible_summaries]
        )
        markets = _load_compact_markets(
            db,
            input_path,
            columns,
            selected_market_ids=selected_ids,
        )
    finally:
        db.close()

    _validate_source_markets(markets)
    eligible_markets = len(eligible_summaries)
    eligible_propositions = sum(
        int(cast(int, row["outcome_count"])) for row in eligible_summaries
    )
    selection: dict[str, object] = {
        "strategy": (
            "volume_desc_then_market_id"
            if max_propositions is not None
            else "all_eligible_markets"
        ),
        "input_market_rows": input_rows,
        "input_rows": input_rows,
        "input_propositions": input_propositions,
        "invalid_market_rows": invalid_market_rows,
        "eligible_markets": eligible_markets,
        "eligible_propositions": eligible_propositions,
        "selected_markets": len(markets),
        "selected_propositions": sum(len(market.outcomes) for market in markets),
        "truncated": len(markets) < eligible_markets,
    }
    return SOURCE_SCHEMA, input_rows, markets, selection


_COMPACT_REQUIRED_COLUMNS = {
    "market_id",
    "question",
    "outcomes",
    "clob_token_ids",
}
_WC2026_REQUIRED_COLUMNS = {
    "market_id",
    "outcome_index",
    "clob_token_id",
    "question",
    "outcome_label",
    "event_slug",
    "is_active",
    "is_closed",
    "market_volume_usd",
    "stage_key",
    "stage_rank",
    "canonical_team_name",
    "market_direction",
    "progression_outcome_label",
    "is_progression_token",
    "opposite_clob_token_id",
    "market_status",
    "is_still_alive",
    "end_date",
    "odds_hour_utc",
    "odds_hour_epoch",
}
_WC2026_STAGE_RANKS = {
    "round_of_32": 0,
    "round_of_16": 1,
    "quarterfinal": 2,
    "semifinal": 3,
    "final": 4,
    "winner": 5,
}
_WC2026_PROGRESSION_OUTCOMES = {
    ("round_of_32", "advance"): "reach_round_of_32",
    ("round_of_16", "advance"): "reach_round_of_16",
    ("quarterfinal", "advance"): "reach_quarterfinal",
    ("semifinal", "advance"): "reach_semifinal",
    ("final", "advance"): "reach_final",
    ("winner", "winner"): "win_world_cup",
    ("round_of_32", "elimination"): "not_eliminated_in_round_of_32",
    ("round_of_16", "elimination"): "not_eliminated_in_round_of_16",
}
_WC2026_EXPORT_GUIDANCE = (
    " Re-export with oddsfox-pipeline/scripts/"
    "export_polymarket_wc2026_graph_hourly_odds.py."
)


def _wc2026_error(message: str) -> ValueError:
    return ValueError(message + _WC2026_EXPORT_GUIDANCE)


def resolve_input_profile(input_path: Path, requested: InputProfile = "auto") -> str:
    """Resolve exactly one supported schema from column contracts."""

    if requested not in {"auto", SOURCE_SCHEMA, WC2026_SOURCE_SCHEMA}:
        raise ValueError(f"Unsupported input profile: {requested}")
    db = DuckDB()
    try:
        schema = {
            str(row["column_name"]).lower(): str(row["column_type"]).upper()
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(input_path)}')"
            )
        }
    finally:
        db.close()
    columns = set(schema)
    matches = [
        profile
        for profile, required in (
            (SOURCE_SCHEMA, _COMPACT_REQUIRED_COLUMNS),
            (WC2026_SOURCE_SCHEMA, _WC2026_REQUIRED_COLUMNS),
        )
        if required <= columns
    ]
    if requested != "auto":
        required = (
            _WC2026_REQUIRED_COLUMNS
            if requested == WC2026_SOURCE_SCHEMA
            else _COMPACT_REQUIRED_COLUMNS
        )
        missing = sorted(required - columns)
        if missing:
            error = ValueError(
                f"Input does not match {requested}; missing columns: "
                + ", ".join(missing)
            )
            if requested == WC2026_SOURCE_SCHEMA:
                error = _wc2026_error(str(error))
            raise error
        return requested
    if len(matches) != 1:
        if not matches:
            raise ValueError(
                "Discovery input does not match a known schema. Supported profiles: "
                f"{SOURCE_SCHEMA}, {WC2026_SOURCE_SCHEMA}. Pass --input-profile "
                "after exporting a supported Parquet contract"
            )
        raise ValueError(
            "Discovery input matches multiple schemas; pass --input-profile explicitly: "
            + ", ".join(sorted(matches))
        )
    return matches[0]


def _load_wc2026_markets(
    input_path: Path,
) -> tuple[str, int, list[SourceMarket], dict[str, object]]:
    db = DuckDB()
    try:
        db.execute("SET TimeZone = 'UTC'")
        schema = {
            str(row["column_name"]).lower(): str(row["column_type"]).upper()
            for row in db.rows(
                f"DESCRIBE SELECT * FROM read_parquet('{q(input_path)}')"
            )
        }
        _validate_wc2026_column_types(schema)
        input_rows = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(input_path)}')") or 0
        )
        if input_rows == 0:
            raise _wc2026_error("WC2026 graph input contains no hourly rows")
        invalid_ids = _wc2026_invalid_required_rows(db, input_path)
        if invalid_ids:
            raise _wc2026_error(
                "WC2026 graph input has null or empty required fields for: "
                + ", ".join(invalid_ids)
            )
        invalid_hours = _bounded_values(
            db,
            f"""
            SELECT market_id || '/' || clob_token_id || '/' || odds_hour_epoch AS value
            FROM read_parquet('{q(input_path)}')
            WHERE odds_hour_epoch % 3600 != 0
               OR epoch(odds_hour_utc) != odds_hour_epoch::DOUBLE
               OR date_trunc('hour', odds_hour_utc) != odds_hour_utc
               OR market_volume_usd < 0
               OR NOT isfinite(market_volume_usd)
            ORDER BY value
            """,
        )
        if invalid_hours:
            raise _wc2026_error(
                "WC2026 graph input has invalid hourly grain or volume for: "
                + ", ".join(invalid_hours)
            )
        invalid_close_times = _bounded_values(
            db,
            f"""
            SELECT market_id || '/' || clob_token_id AS value
            FROM read_parquet('{q(input_path)}')
            WHERE NOT isfinite(end_date)
            ORDER BY value
            """,
        )
        if invalid_close_times:
            raise _wc2026_error(
                "WC2026 graph input has a non-finite end_date for: "
                + ", ".join(invalid_close_times)
            )
        duplicate_grains = _bounded_values(
            db,
            f"""
            SELECT market_id || '/' || clob_token_id || '/' || odds_hour_epoch AS value
            FROM read_parquet('{q(input_path)}')
            GROUP BY market_id, clob_token_id, odds_hour_epoch
            HAVING count(*) > 1
            ORDER BY value
            """,
        )
        if duplicate_grains:
            raise _wc2026_error(
                "WC2026 graph input has duplicate market/token/hour rows for: "
                + ", ".join(duplicate_grains)
            )
        semantic_rows = db.rows(
            f"""
            SELECT DISTINCT
                market_id::VARCHAR AS market_id,
                outcome_index::INTEGER AS outcome_index,
                clob_token_id::VARCHAR AS clob_token_id,
                question::VARCHAR AS question,
                outcome_label::VARCHAR AS outcome_label,
                event_slug::VARCHAR AS event_slug,
                is_active::BOOLEAN AS is_active,
                is_closed::BOOLEAN AS is_closed,
                market_volume_usd::DOUBLE AS market_volume_usd,
                stage_key::VARCHAR AS stage_key,
                stage_rank::INTEGER AS stage_rank,
                canonical_team_name::VARCHAR AS canonical_team_name,
                market_direction::VARCHAR AS market_direction,
                progression_outcome_label::VARCHAR AS progression_outcome_label,
                is_progression_token::BOOLEAN AS is_progression_token,
                opposite_clob_token_id::VARCHAR AS opposite_clob_token_id,
                market_status::VARCHAR AS market_status,
                is_still_alive::BOOLEAN AS is_still_alive,
                end_date::TIMESTAMPTZ AS market_close_time
            FROM read_parquet('{q(input_path)}')
            ORDER BY market_id, outcome_index, clob_token_id
            """
        )
        observation_rows = db.rows(
            f"""
            SELECT market_id::VARCHAR AS market_id,
                   min(try_cast(odds_hour_utc AS TIMESTAMPTZ)) AS first_seen_ts,
                   max(try_cast(odds_hour_utc AS TIMESTAMPTZ)) AS last_seen_ts,
                   min(odds_hour_epoch)::BIGINT AS first_hour_epoch,
                   max(odds_hour_epoch)::BIGINT AS last_hour_epoch
            FROM read_parquet('{q(input_path)}')
            GROUP BY market_id
            ORDER BY market_id
            """
        )
    finally:
        db.close()

    observations = {str(row["market_id"]): row for row in observation_rows}
    markets = _wc2026_source_markets(semantic_rows, observations)
    semantic_payload = [_wc2026_market_semantics(market) for market in markets]
    stage_keys = sorted({market.stage_key for market in markets if market.stage_key})
    team_names = sorted({market.team_name for market in markets if market.team_name})
    first_epoch = min(
        _required_int(row["first_hour_epoch"], "first_hour_epoch")
        for row in observation_rows
    )
    last_epoch = max(
        _required_int(row["last_hour_epoch"], "last_hour_epoch")
        for row in observation_rows
    )
    selection: dict[str, object] = {
        "strategy": "all_valid_pipeline_wc2026_markets",
        "source": "oddsfox-pipeline",
        "scope": "wc2026",
        "universe": "knockout_progression",
        "selection": "all_valid_pipeline_wc2026_markets",
        "adapter_version": INPUT_ADAPTER_VERSION,
        "input_hourly_rows": input_rows,
        "input_rows": input_rows,
        "input_market_rows": len(markets),
        "input_propositions": len(markets) * 2,
        "invalid_market_rows": 0,
        "eligible_markets": len(markets),
        "eligible_propositions": len(markets) * 2,
        "selected_markets": len(markets),
        "selected_propositions": len(markets) * 2,
        "teams": len(team_names),
        "stages": len(stage_keys),
        "stage_keys": stage_keys,
        "first_hour_epoch": first_epoch,
        "last_hour_epoch": last_epoch,
        "normalized_semantic_fingerprint": canonical_json_sha256(semantic_payload),
        "truncated": False,
    }
    return WC2026_SOURCE_SCHEMA, input_rows, markets, selection


def _validate_wc2026_column_types(schema: dict[str, str]) -> None:
    allowed = {
        "market_id": {"VARCHAR"},
        "outcome_index": {"INTEGER", "BIGINT", "SMALLINT", "TINYINT"},
        "clob_token_id": {"VARCHAR"},
        "question": {"VARCHAR"},
        "outcome_label": {"VARCHAR"},
        "event_slug": {"VARCHAR"},
        "is_active": {"BOOLEAN"},
        "is_closed": {"BOOLEAN"},
        "market_volume_usd": {"DOUBLE", "FLOAT", "DECIMAL"},
        "stage_key": {"VARCHAR"},
        "stage_rank": {"INTEGER", "BIGINT", "SMALLINT", "TINYINT"},
        "canonical_team_name": {"VARCHAR"},
        "market_direction": {"VARCHAR"},
        "progression_outcome_label": {"VARCHAR"},
        "is_progression_token": {"BOOLEAN"},
        "opposite_clob_token_id": {"VARCHAR"},
        "market_status": {"VARCHAR"},
        "is_still_alive": {"BOOLEAN"},
        "odds_hour_epoch": {"BIGINT", "INTEGER", "UBIGINT", "UINTEGER"},
    }
    wrong = {}
    for name, accepted in allowed.items():
        actual = schema[name]
        if not any(
            actual == value or actual.startswith(value + "(")
            for value in accepted
        ):
            wrong[name] = actual
    if wrong:
        raise _wc2026_error(
            "WC2026 graph input has incompatible column types: "
            + ", ".join(f"{name}={value}" for name, value in sorted(wrong.items()))
        )
    if not schema["odds_hour_utc"].startswith("TIMESTAMP"):
        raise _wc2026_error(
            "WC2026 graph input has incompatible column type: "
            f"odds_hour_utc={schema['odds_hour_utc']}"
        )
    if not schema["end_date"].startswith("TIMESTAMP"):
        raise _wc2026_error(
            "WC2026 graph input has incompatible column type: "
            f"end_date={schema['end_date']}"
        )


def _wc2026_invalid_required_rows(db: DuckDB, input_path: Path) -> list[str]:
    text_columns = (
        "market_id",
        "clob_token_id",
        "question",
        "outcome_label",
        "event_slug",
        "canonical_team_name",
        "stage_key",
        "market_direction",
        "progression_outcome_label",
        "opposite_clob_token_id",
        "market_status",
    )
    empty = " OR ".join(f"trim({name}::VARCHAR) = ''" for name in text_columns)
    nulls = " OR ".join(
        f"{name} IS NULL" for name in sorted(_WC2026_REQUIRED_COLUMNS)
    )
    return _bounded_values(
        db,
        f"""
        SELECT coalesce(market_id::VARCHAR, '<null>') || '/' ||
               coalesce(clob_token_id::VARCHAR, '<null>') AS value
        FROM read_parquet('{q(input_path)}')
        WHERE {nulls} OR {empty}
        ORDER BY value
        """,
    )


def _bounded_values(db: DuckDB, sql: str, *, limit: int = 10) -> list[str]:
    return [str(row["value"]) for row in db.rows(f"SELECT * FROM ({sql}) LIMIT {limit}")]


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"WC2026 {field} must be an integer")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"WC2026 {field} must be an integer") from exc


def _wc2026_source_markets(
    semantic_rows: Sequence[dict[str, object]],
    observations: dict[str, dict[str, object]],
) -> list[SourceMarket]:
    by_market: dict[str, list[dict[str, object]]] = {}
    token_owner: dict[str, str] = {}
    for row in semantic_rows:
        market_id = str(row["market_id"])
        token = str(row["clob_token_id"])
        owner = token_owner.setdefault(token, market_id)
        if owner != market_id:
            raise _wc2026_error(
                f"WC2026 clob_token_id {token!r} belongs to multiple markets"
            )
        by_market.setdefault(market_id, []).append(row)

    markets: list[SourceMarket] = []
    errors: list[str] = []
    for market_id, rows in sorted(by_market.items()):
        if len(rows) != 2:
            errors.append(f"{market_id}: expected 2 invariant token rows, found {len(rows)}")
            continue
        indexes = {
            _required_int(row["outcome_index"], "outcome_index") for row in rows
        }
        labels = {str(row["outcome_label"]) for row in rows}
        tokens = {str(row["clob_token_id"]) for row in rows}
        progression_rows = [row for row in rows if bool(row["is_progression_token"])]
        invariant_fields = (
            "question",
            "event_slug",
            "is_active",
            "is_closed",
            "market_volume_usd",
            "stage_key",
            "stage_rank",
            "canonical_team_name",
            "market_direction",
            "progression_outcome_label",
            "market_status",
            "is_still_alive",
            "market_close_time",
        )
        non_invariant = [
            field
            for field in invariant_fields
            if len({row[field] for row in rows}) != 1
        ]
        if indexes != {0, 1} or labels != {"Yes", "No"} or len(tokens) != 2:
            errors.append(f"{market_id}: tokens must be unique literal Yes/No indexes 0/1")
            continue
        if len(progression_rows) != 1:
            errors.append(f"{market_id}: expected exactly one progression token")
            continue
        if non_invariant:
            errors.append(f"{market_id}: non-invariant {','.join(non_invariant)}")
            continue
        if any(
            str(row["opposite_clob_token_id"]) not in tokens
            or str(row["opposite_clob_token_id"]) == str(row["clob_token_id"])
            for row in rows
        ):
            errors.append(f"{market_id}: opposite token links are not reciprocal")
            continue
        stage_key = str(rows[0]["stage_key"])
        stage_rank = _required_int(rows[0]["stage_rank"], "stage_rank")
        direction = str(rows[0]["market_direction"])
        progression_outcome = str(rows[0]["progression_outcome_label"])
        market_status = str(rows[0]["market_status"])
        if _WC2026_STAGE_RANKS.get(stage_key) != stage_rank:
            errors.append(f"{market_id}: invalid stage_key/stage_rank")
            continue
        if direction not in {"winner", "advance", "elimination"}:
            errors.append(f"{market_id}: invalid market_direction {direction!r}")
            continue
        if market_status not in {"resolved", "closed", "live", "inactive"}:
            errors.append(f"{market_id}: invalid market_status {market_status!r}")
            continue
        expected_status = (
            "closed"
            if bool(rows[0]["is_closed"])
            else "live" if bool(rows[0]["is_active"]) else "inactive"
        )
        if market_status != "resolved" and market_status != expected_status:
            errors.append(f"{market_id}: market_status conflicts with active/closed flags")
            continue
        expected_outcome = _WC2026_PROGRESSION_OUTCOMES.get((stage_key, direction))
        if expected_outcome != progression_outcome:
            errors.append(f"{market_id}: invalid progression_outcome_label")
            continue
        expected_label = "No" if direction == "elimination" else "Yes"
        if str(progression_rows[0]["outcome_label"]) != expected_label:
            errors.append(f"{market_id}: progression-token orientation is invalid")
            continue
        progression_level = stage_rank + (1 if direction == "elimination" else 0)
        observation = observations[market_id]
        source_outcomes = tuple(
            SourceOutcome(
                outcome_index=_required_int(row["outcome_index"], "outcome_index"),
                outcome=str(row["outcome_label"]),
                clob_token_id=str(row["clob_token_id"]),
                is_progression=bool(row["is_progression_token"]),
                opposite_clob_token_id=str(row["opposite_clob_token_id"]),
            )
            for row in sorted(
                rows,
                key=lambda item: _required_int(
                    item["outcome_index"], "outcome_index"
                ),
            )
        )
        market_fields = {
            "market_id": market_id,
            **{field: rows[0][field] for field in invariant_fields},
            "progression_level": progression_level,
            "outcomes": [outcome.__dict__ for outcome in source_outcomes],
        }
        markets.append(
            SourceMarket(
                market_id=market_id,
                question=str(rows[0]["question"]),
                description="",
                outcomes=source_outcomes,
                source_hash=source_market_hash(market_fields),
                event_slug=str(rows[0]["event_slug"]),
                category="sports",
                tags=("fifa-world-cup-2026", "knockout-progression"),
                is_active=bool(rows[0]["is_active"]),
                is_closed=bool(rows[0]["is_closed"]),
                first_seen_ts=datetime_or_none(observation["first_seen_ts"]),
                last_seen_ts=datetime_or_none(observation["last_seen_ts"]),
                market_close_time=datetime_or_none(rows[0]["market_close_time"]),
                volume=float(cast(float, rows[0]["market_volume_usd"])),
                input_profile=WC2026_SOURCE_SCHEMA,
                team_name=str(rows[0]["canonical_team_name"]),
                stage_key=stage_key,
                stage_rank=stage_rank,
                progression_level=progression_level,
                market_direction=direction,
                progression_outcome=progression_outcome,
                market_status=str(rows[0]["market_status"]),
                is_still_alive=bool(rows[0]["is_still_alive"]),
            )
        )
    if errors:
        suffix = "" if len(errors) <= 10 else f" (+{len(errors) - 10} more)"
        raise _wc2026_error(
            "WC2026 graph input failed market validation: "
            + "; ".join(errors[:10])
            + suffix
        )
    _validate_source_markets(markets)
    return markets


def _wc2026_market_semantics(market: SourceMarket) -> dict[str, Any]:
    return {
        "market_id": market.market_id,
        "question": market.question,
        "event_slug": market.event_slug,
        "is_active": market.is_active,
        "is_closed": market.is_closed,
        "market_volume_usd": market.volume,
        "team_name": market.team_name,
        "stage_key": market.stage_key,
        "stage_rank": market.stage_rank,
        "progression_level": market.progression_level,
        "market_direction": market.market_direction,
        "progression_outcome": market.progression_outcome,
        "market_status": market.market_status,
        "is_still_alive": market.is_still_alive,
        "market_close_time": market.market_close_time,
        "outcomes": [outcome.__dict__ for outcome in market.outcomes],
    }


def _load_compact_markets(
    db: DuckDB,
    input_path: Path,
    columns: set[str],
    *,
    selected_market_ids: Sequence[str] | None = None,
) -> list[SourceMarket]:
    selection_sql = (
        "WHERE market_id::VARCHAR IN (SELECT unnest(?))"
        if selected_market_ids is not None
        else ""
    )
    params: Sequence[object] | None = (
        [list(selected_market_ids)]
        if selected_market_ids is not None
        else None
    )
    rows = db.rows(
        f"""
        SELECT
            market_id::VARCHAR AS market_id,
            question::VARCHAR AS question,
            {_optional_sql(columns, "description", "VARCHAR", "''::VARCHAR")},
            outcomes,
            clob_token_ids,
            {_optional_sql(columns, "event_id", "VARCHAR")},
            {_optional_sql(columns, "event_slug", "VARCHAR")},
            {_optional_sql(columns, "category", "VARCHAR")},
            {_optional_sql(columns, "tags", "VARCHAR[]", "[]::VARCHAR[]")},
            {_optional_sql(columns, "volume", "DOUBLE")},
            {_timestamp_sql(columns, ("start_time",), "time_start")},
            {_timestamp_sql(columns, ("end_time",), "time_end")}
        FROM read_parquet('{q(input_path)}')
        {selection_sql}
        ORDER BY market_id
        """,
        params,
    )
    markets = []
    for row in rows:
        outcomes = list(cast(Sequence[object], row["outcomes"] or []))
        tokens = list(cast(Sequence[object], row["clob_token_ids"] or []))
        if (
            row["market_id"] is None
            or row["question"] is None
            or len(outcomes) != len(tokens)
            or not outcomes
            or any(
                value is None or not str(value).strip()
                for value in (*outcomes, *tokens)
            )
        ):
            raise ValueError(
                f"Market {row['market_id']!r} must have equal-length outcomes "
                "and clob_token_ids containing only non-empty values"
            )
        market_id = str(row["market_id"])
        question = str(row["question"])
        description = str(row.get("description") or "")
        source_outcomes = tuple(
            SourceOutcome(index, str(outcome), str(token))
            for index, (outcome, token) in enumerate(
                zip(outcomes, tokens, strict=True)
            )
        )
        source_fields = {
            "market_id": market_id,
            "question": question,
            "description": description,
            "event_id": str_or_none(row.get("event_id")),
            "event_slug": str_or_none(row.get("event_slug")),
            "category": str_or_none(row.get("category")),
            "tags": [
                str(tag)
                for tag in cast(Sequence[object], row.get("tags") or [])
            ],
            "time_start": datetime_or_none(row.get("time_start")),
            "time_end": datetime_or_none(row.get("time_end")),
            "outcomes": [
                {
                    "outcome_index": item.outcome_index,
                    "outcome": item.outcome,
                    "clob_token_id": item.clob_token_id,
                }
                for item in source_outcomes
            ],
        }
        markets.append(
            SourceMarket(
                market_id=market_id,
                question=question,
                description=description,
                source_hash=source_market_hash(source_fields),
                event_id=str_or_none(row.get("event_id")),
                event_slug=str_or_none(row.get("event_slug")),
                category=str_or_none(row.get("category")),
                tags=tuple(
                    str(tag)
                    for tag in cast(Sequence[object], row.get("tags") or [])
                ),
                time_start=datetime_or_none(row.get("time_start")),
                time_end=datetime_or_none(row.get("time_end")),
                volume=(
                    float(cast(float, row["volume"]))
                    if row.get("volume") is not None
                    else None
                ),
                outcomes=source_outcomes,
            )
        )
    return markets


def _compact_market_summaries(
    db: DuckDB,
    input_path: Path,
    columns: set[str],
) -> list[dict[str, object]]:
    volume_sql = (
        "try_cast(volume AS DOUBLE)"
        if "volume" in columns
        else "NULL::DOUBLE"
    )
    return db.rows(
        f"""
        SELECT
            market_id::VARCHAR AS market_id,
            {volume_sql} AS volume,
            len(outcomes)::INTEGER AS outcome_count
        FROM read_parquet('{q(input_path)}')
        WHERE market_id IS NOT NULL
          AND question IS NOT NULL
          AND outcomes IS NOT NULL
          AND clob_token_ids IS NOT NULL
          AND len(outcomes) > 0
          AND len(outcomes) = len(clob_token_ids)
        ORDER BY market_id
        """
    )


def _select_market_summaries(
    summaries: Sequence[dict[str, object]],
    max_propositions: int,
) -> list[str]:
    selected: list[str] = []
    selected_propositions = 0
    ordered = sorted(
        summaries,
        key=lambda row: (
            -(
                float(cast(float, row["volume"]))
                if row.get("volume") is not None
                else float("-inf")
            ),
            str(row["market_id"]),
        ),
    )
    for row in ordered:
        next_count = selected_propositions + int(
            cast(int, row["outcome_count"])
        )
        if next_count > max_propositions:
            continue
        selected.append(str(row["market_id"]))
        selected_propositions = next_count
        if selected_propositions == max_propositions:
            break
    if not selected:
        raise ValueError(
            "No complete market fits within max_propositions="
            f"{max_propositions}"
        )
    return sorted(selected)


def _validate_compact_catalog(db: DuckDB, input_path: Path) -> None:
    valid = f"""
        SELECT *
        FROM read_parquet('{q(input_path)}')
        WHERE market_id IS NOT NULL
          AND question IS NOT NULL
          AND outcomes IS NOT NULL
          AND clob_token_ids IS NOT NULL
          AND len(outcomes) > 0
          AND len(outcomes) = len(clob_token_ids)
    """
    duplicate_markets = int(
        db.scalar(
            f"""
            SELECT count(*)
            FROM (
                SELECT market_id
                FROM ({valid})
                GROUP BY market_id
                HAVING count(*) > 1
            )
            """
        )
        or 0
    )
    if duplicate_markets:
        raise ValueError(
            f"Discovery input contains {duplicate_markets} duplicate market_id values"
        )
    malformed = int(
        db.scalar(
            f"""
            SELECT count(*)
            FROM ({valid})
            WHERE trim(market_id::VARCHAR) = ''
               OR trim(question::VARCHAR) = ''
               OR list_unique(outcomes) != len(outcomes)
               OR EXISTS (
                    SELECT 1
                    FROM unnest(outcomes) AS outcome(value)
                    WHERE value IS NULL OR trim(value::VARCHAR) = ''
               )
               OR EXISTS (
                    SELECT 1
                    FROM unnest(clob_token_ids) AS token(value)
                    WHERE value IS NULL OR trim(value::VARCHAR) = ''
               )
            """
        )
        or 0
    )
    if malformed:
        raise ValueError(
            f"Discovery input contains {malformed} malformed eligible market rows"
        )
    duplicate_tokens = int(
        db.scalar(
            f"""
            SELECT count(*)
            FROM (
                SELECT token
                FROM ({valid}),
                unnest(clob_token_ids) AS value(token)
                GROUP BY token
                HAVING count(*) > 1
            )
            """
        )
        or 0
    )
    if duplicate_tokens:
        raise ValueError(
            f"Discovery input contains {duplicate_tokens} duplicate clob_token_id values"
        )


def _optional_sql(
    columns: set[str],
    name: str,
    sql_type: str,
    fallback: str | None = None,
) -> str:
    if name in columns:
        return f"{name} AS {name}"
    return f"{fallback or f'NULL::{sql_type}'} AS {name}"


def _timestamp_sql(
    columns: set[str],
    candidates: Sequence[str],
    alias: str,
) -> str:
    for name in candidates:
        if name in columns:
            return f"try_cast({name} AS TIMESTAMPTZ) AS {alias}"
    return f"NULL::TIMESTAMPTZ AS {alias}"


def _validate_source_markets(markets: Sequence[SourceMarket]) -> None:
    if not markets:
        raise ValueError("Discovery input contains no markets")
    market_ids: set[str] = set()
    proposition_ids: set[str] = set()
    for market in markets:
        if not market.market_id or not market.question:
            raise ValueError("market_id and question must be non-empty")
        if market.market_id in market_ids:
            raise ValueError(f"Duplicate market_id {market.market_id!r}")
        market_ids.add(market.market_id)
        outcomes = [outcome.outcome for outcome in market.outcomes]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError(f"Market {market.market_id!r} has duplicate outcomes")
        for outcome in market.outcomes:
            if not outcome.clob_token_id:
                raise ValueError(
                    f"Market {market.market_id!r} has an empty clob_token_id"
                )
            if outcome.clob_token_id in proposition_ids:
                raise ValueError(
                    f"clob_token_id {outcome.clob_token_id!r} belongs to multiple outcomes"
                )
            proposition_ids.add(outcome.clob_token_id)


def source_market_hash(fields: object) -> str:
    return canonical_json_sha256(fields)


def str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return utc_datetime(parsed)


def utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
