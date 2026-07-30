from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .contracts import SourceMarket, SourceOutcome
from ..queries import DuckDB, q


def load_source_markets(
    input_path: Path,
    *,
    max_propositions: int | None = None,
) -> tuple[str, int, list[SourceMarket], dict[str, object]]:
    db = DuckDB()
    eligible_summaries: list[dict[str, object]] | None = None
    try:
        db.execute("SET TimeZone = 'UTC'")
        columns = {
            str(row["name"]).lower()
            for row in db.rows(
                f"SELECT name FROM parquet_schema('{q(input_path)}') "
                "WHERE name != 'duckdb_schema'"
            )
        }
        input_rows = int(
            db.scalar(f"SELECT count(*) FROM read_parquet('{q(input_path)}')") or 0
        )
        if {"market_id", "question", "outcomes", "clob_token_ids"} <= columns:
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
            if max_propositions is not None:
                _validate_compact_catalog(db, input_path)
                eligible_summaries = _compact_market_summaries(
                    db,
                    input_path,
                    columns,
                )
                selected_ids = _select_market_summaries(
                    eligible_summaries,
                    max_propositions,
                )
                markets = _load_compact_markets(
                    db,
                    input_path,
                    columns,
                    selected_market_ids=selected_ids,
                )
            else:
                markets = _load_compact_markets(db, input_path, columns)
            source_format = "market_snapshot"
        elif {
            "market_id",
            "question",
            "outcome_label",
            "clob_token_id",
            "event_slug",
        } <= columns and (
            "odds_timestamp_epoch" in columns or "odds_hour_epoch" in columns
        ):
            markets = _load_odds_markets(db, input_path, columns)
            invalid_market_rows = 0
            input_propositions = sum(len(market.outcomes) for market in markets)
            source_format = (
                "minutely"
                if "odds_timestamp_epoch" in columns
                else "hourly"
            )
        else:
            raise ValueError(
                "Discovery input must be a compact market snapshot or a supported "
                "OddsFox minutely/hourly export"
            )
    finally:
        db.close()

    _validate_source_markets(markets)
    if source_format == "market_snapshot" and eligible_summaries is not None:
        eligible_markets = len(eligible_summaries)
        eligible_propositions = sum(
            int(row["outcome_count"]) for row in eligible_summaries
        )
    else:
        eligible_markets = len(markets)
        eligible_propositions = sum(len(market.outcomes) for market in markets)
    if max_propositions is not None and eligible_summaries is None:
        markets = _select_source_markets(markets, max_propositions)
    selection = {
        "strategy": (
            "volume_desc_then_market_id"
            if max_propositions is not None
            else "all_eligible_markets"
        ),
        "input_market_rows": (
            input_rows if source_format == "market_snapshot" else None
        ),
        "input_rows": input_rows,
        "input_propositions": input_propositions,
        "invalid_market_rows": invalid_market_rows,
        "eligible_markets": eligible_markets,
        "eligible_propositions": eligible_propositions,
        "selected_markets": len(markets),
        "selected_propositions": sum(len(market.outcomes) for market in markets),
        "truncated": len(markets) < eligible_markets,
    }
    return source_format, input_rows, markets, selection


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
            outcomes,
            clob_token_ids,
            {_optional_sql(columns, "event_id", "VARCHAR")},
            {_optional_sql(columns, "event_slug", "VARCHAR")},
            {_optional_sql(columns, "category", "VARCHAR")},
            {_optional_sql(columns, "tags", "VARCHAR[]", "[]::VARCHAR[]")},
            {_optional_sql(columns, "volume", "DOUBLE")},
            {_timestamp_sql(columns, ("time_start", "start_time", "start_date"), "time_start")},
            {_timestamp_sql(columns, ("time_end", "end_time", "end_date"), "time_end")}
        FROM read_parquet('{q(input_path)}')
        {selection_sql}
        ORDER BY market_id
        """,
        params,
    )
    markets = []
    for row in rows:
        outcomes = list(row["outcomes"] or [])
        tokens = list(row["clob_token_ids"] or [])
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
        markets.append(
            SourceMarket(
                market_id=str(row["market_id"]),
                question=str(row["question"]),
                event_id=str_or_none(row.get("event_id")),
                event_slug=str_or_none(row.get("event_slug")),
                category=str_or_none(row.get("category")),
                tags=tuple(str(tag) for tag in (row.get("tags") or [])),
                time_start=datetime_or_none(row.get("time_start")),
                time_end=datetime_or_none(row.get("time_end")),
                volume=(
                    float(row["volume"])
                    if row.get("volume") is not None
                    else None
                ),
                outcomes=tuple(
                    SourceOutcome(index, str(outcome), str(token))
                    for index, (outcome, token) in enumerate(
                        zip(outcomes, tokens, strict=True)
                    )
                ),
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
                float(row["volume"])
                if row.get("volume") is not None
                else float("-inf")
            ),
            str(row["market_id"]),
        ),
    )
    for row in ordered:
        next_count = selected_propositions + int(row["outcome_count"])
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


def _select_source_markets(
    markets: Sequence[SourceMarket],
    max_propositions: int,
) -> list[SourceMarket]:
    selected: list[SourceMarket] = []
    selected_propositions = 0
    ordered = sorted(
        markets,
        key=lambda market: (
            -(market.volume if market.volume is not None else float("-inf")),
            market.market_id,
        ),
    )
    for market in ordered:
        next_count = selected_propositions + len(market.outcomes)
        if next_count > max_propositions:
            continue
        selected.append(market)
        selected_propositions = next_count
        if selected_propositions == max_propositions:
            break
    if not selected:
        raise ValueError(
            "No complete market fits within max_propositions="
            f"{max_propositions}"
        )
    return sorted(selected, key=lambda market: market.market_id)


def _load_odds_markets(
    db: DuckDB,
    input_path: Path,
    columns: set[str],
) -> list[SourceMarket]:
    epoch = (
        "odds_timestamp_epoch"
        if "odds_timestamp_epoch" in columns
        else "odds_hour_epoch"
    )
    timestamp = (
        "odds_timestamp" if "odds_timestamp" in columns else "odds_hour_utc"
    )
    rows = db.rows(
        f"""
        WITH ranked AS (
            SELECT
                *,
                min({timestamp}) OVER (PARTITION BY clob_token_id) AS first_seen_ts,
                max({timestamp}) OVER (PARTITION BY clob_token_id) AS last_seen_ts,
                row_number() OVER (
                    PARTITION BY clob_token_id
                    ORDER BY {epoch} DESC
                ) AS rn
            FROM read_parquet('{q(input_path)}')
        )
        SELECT
            market_id::VARCHAR AS market_id,
            outcome_index::INTEGER AS outcome_index,
            clob_token_id::VARCHAR AS clob_token_id,
            question::VARCHAR AS question,
            outcome_label::VARCHAR AS outcome,
            event_slug::VARCHAR AS event_slug,
            {_optional_sql(columns, "event_id", "VARCHAR")},
            {_optional_sql(columns, "category", "VARCHAR")},
            {_optional_sql(columns, "tags", "VARCHAR[]", "[]::VARCHAR[]")},
            {_optional_sql(columns, "market_volume_usd", "DOUBLE")},
            is_active::BOOLEAN AS is_active,
            is_closed::BOOLEAN AS is_closed,
            first_seen_ts,
            last_seen_ts
        FROM ranked
        WHERE rn = 1
        ORDER BY market_id, outcome_index, clob_token_id
        """
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["market_id"])].append(row)
    markets = []
    for market_id in sorted(grouped):
        items = grouped[market_id]
        first = items[0]
        markets.append(
            SourceMarket(
                market_id=market_id,
                question=str(first["question"]),
                event_id=str_or_none(first.get("event_id")),
                event_slug=str_or_none(first.get("event_slug")),
                category=str_or_none(first.get("category")),
                tags=tuple(str(tag) for tag in (first.get("tags") or [])),
                is_active=any(bool(item["is_active"]) for item in items),
                is_closed=any(bool(item["is_closed"]) for item in items),
                first_seen_ts=min(
                    (
                        item["first_seen_ts"]
                        for item in items
                        if item["first_seen_ts"] is not None
                    ),
                    default=None,
                ),
                last_seen_ts=max(
                    (
                        item["last_seen_ts"]
                        for item in items
                        if item["last_seen_ts"] is not None
                    ),
                    default=None,
                ),
                volume=max(
                    (
                        float(item["market_volume_usd"])
                        for item in items
                        if item.get("market_volume_usd") is not None
                    ),
                    default=None,
                ),
                outcomes=tuple(
                    SourceOutcome(
                        int(item["outcome_index"]),
                        str(item["outcome"]),
                        str(item["clob_token_id"]),
                    )
                    for item in items
                ),
            )
        )
    return markets


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
