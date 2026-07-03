# selected_token_hourly_odds

## Overview

- **Source mart:** `polymarket_marts.selected_token_hourly_odds`
- **Grain:** one row per `(clob_token_id, odds_hour_utc)`
- **Exported at (UTC):** 2026-07-03T09:52:22Z
- **Parquet file:** `selected_token_hourly_odds_20260703T095031Z.parquet`

## Snapshot

| Metric | Value |
| --- | --- |
| Rows | 1,094,140 |
| Markets | 1,865 |
| Tokens | 3,730 |
| Time range start (UTC) | 2025-07-02T22:00:00+00:00 |
| Time range end (UTC) | 2026-07-03T09:00:00+00:00 |
| Price min | 0.0005 |
| Price max | 0.9995 |
| Observed points min | 1 |
| Observed points max | 120 |
| Null outcome_label rows | 0 |

## Schema

| Column | Type | Description |
| --- | --- | --- |
| `market_id` | `VARCHAR` | Polymarket market identifier. |
| `outcome_index` | `INTEGER` | Zero-based outcome index within the market token list. |
| `clob_token_id` | `VARCHAR` | CLOB outcome token identifier (grain key). |
| `question` | `VARCHAR` | Market question or title at export time. |
| `outcome_label` | `VARCHAR` | Resolved outcome label (e.g. Yes/No) from the market outcomes array at outcome_index. |
| `event_slug` | `VARCHAR` | Polymarket event slug for the selected market scope. |
| `is_active` | `BOOLEAN` | Whether the market is active at export time. |
| `is_closed` | `BOOLEAN` | Whether the market is closed at export time. |
| `market_volume_usd` | `DOUBLE` | Reported market volume (USD) at build time. |
| `odds_hour_utc` | `TIMESTAMP WITH TIME ZONE` | UTC hour bucket for the aggregated odds row (grain key). |
| `odds_hour_epoch` | `BIGINT` | Unix epoch seconds for the UTC hour bucket. |
| `open_price` | `DOUBLE` | First observed CLOB price in the token-hour. |
| `high_price` | `DOUBLE` | Highest observed CLOB price in the token-hour. |
| `low_price` | `DOUBLE` | Lowest observed CLOB price in the token-hour. |
| `close_price` | `DOUBLE` | Last observed CLOB price in the token-hour. |
| `avg_price` | `DOUBLE` | Average observed CLOB price in the token-hour. |
| `observed_points` | `BIGINT` | Count of canonical odds points rolled into the token-hour. |
| `first_timestamp` | `BIGINT` | Unix epoch seconds for the first observation in the hour. |
| `first_observed_at` | `TIMESTAMP WITH TIME ZONE` | Wall-clock timestamp of the first observation in the hour. |
| `last_timestamp` | `BIGINT` | Unix epoch seconds for the last observation in the hour. |
| `last_observed_at` | `TIMESTAMP WITH TIME ZONE` | Wall-clock timestamp of the last observation in the hour. |

## Outcome labels (top 10)

| outcome_label | rows |
| --- | --- |
| No | 546,299 |
| Yes | 546,291 |
| Messi | 692 |
| Ronaldo | 692 |
| Dembele | 29 |
| Olise | 29 |
| Kane | 29 |
| Mbappe | 29 |
| Under | 25 |
| Over | 25 |

## Notes

- OHLC prices are Polymarket CLOB implied probabilities in [0, 1].
- `observed_points` counts raw odds observations collapsed into each token-hour.
- `outcome_label` resolves Yes/No (or named outcomes) without joining `selected_markets`.
- `market_volume_usd`, `question`, and market state fields reflect build-time metadata.
- DuckDB may uppercase exported timestamp columns (`ODDS_HOUR_UTC`, `FIRST_OBSERVED_AT`).
