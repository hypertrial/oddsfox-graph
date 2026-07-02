# wc2026_token_minutely_odds

## Overview

- **Source mart:** `polymarket_marts.wc2026_token_minutely_odds`
- **Grain:** one row per `(clob_token_id, odds_timestamp_epoch)`
- **Exported at (UTC):** 2026-07-02T07:08:50Z
- **Parquet file:** `wc2026_token_minutely_odds_20260702T070755Z.parquet`

## Snapshot

| Metric | Value |
| --- | --- |
| Rows | 53,827,798 |
| Markets | 2,344 |
| Tokens | 4,688 |
| Time range start (UTC) | 2025-07-02T22:40:08+00:00 |
| Time range end (UTC) | 2026-07-01T20:14:18+00:00 |
| Price min | 0.0005 |
| Price max | 0.9995 |
| Null outcome_label rows | 0 |

## Schema

| Column | Type | Description |
| --- | --- | --- |
| `market_id` | `VARCHAR` | Polymarket market identifier. |
| `outcome_index` | `INTEGER` | Zero-based outcome index within the market token list. |
| `clob_token_id` | `VARCHAR` | CLOB outcome token identifier (grain key). |
| `question` | `VARCHAR` | Market question or title at export time. |
| `outcome_label` | `VARCHAR` | Resolved outcome label (e.g. Yes/No) from the market outcomes array at outcome_index. |
| `event_slug` | `VARCHAR` | Polymarket event slug (WC2026 scope). |
| `is_active` | `BOOLEAN` | Whether the market is active at export time. |
| `is_closed` | `BOOLEAN` | Whether the market is closed at export time. |
| `market_volume_usd` | `DOUBLE` | Reported market volume (USD) at build time. |
| `ODDS_TIMESTAMP` | `TIMESTAMP WITH TIME ZONE` | Wall-clock timestamp of the minutely odds observation. |
| `ODDS_TIMESTAMP_EPOCH` | `BIGINT` | Unix epoch seconds for the observation (grain key). |
| `price` | `DOUBLE` | Outcome implied probability in [0, 1] (Polymarket CLOB price). |

## Outcome labels (top 10)

| outcome_label | rows |
| --- | --- |
| Yes | 26,899,727 |
| No | 26,849,815 |
| Messi | 39,043 |
| Ronaldo | 39,043 |
| Olise | 30 |
| Mbappe | 30 |
| Kane | 30 |
| Dembele | 29 |
| Under | 26 |
| Over | 25 |

## Notes

- `outcome_label` resolves Yes/No (or named outcomes) without joining `wc2026_markets`.
- `market_volume_usd`, `question`, and market state fields reflect build-time metadata.
- DuckDB may uppercase exported timestamp columns (`ODDS_TIMESTAMP`, `ODDS_TIMESTAMP_EPOCH`).
