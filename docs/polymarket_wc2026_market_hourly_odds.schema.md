# Polymarket WC2026 market hourly odds — parquet schema

Pipeline golden mart export used as the primary input for OddsFox Graph.

| Property | Value |
| --- | --- |
| Relation | `polymarket_wc2026_marts.polymarket_wc2026_market_hourly_odds` |
| Source file pattern | `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet` |
| Example export | `polymarket_wc2026_market_hourly_odds_20260805T183112Z.parquet` |
| Row count (example) | 5,955,270 |
| Column count | 46 |

## Grain

One row per `(market_id, odds_hour_epoch)`.

Each row carries primary-outcome hourly OHLC odds plus market and event metadata.

## Columns

### Grain

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `market_id` | `string` | yes | Polymarket market identifier. |
| `odds_hour_epoch` | `int64` | yes | Hour bucket start as Unix epoch seconds (UTC). |

### Market identity

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `clob_token_id` | `string` | yes | CLOB token ID for the primary outcome. |
| `primary_outcome_label` | `string` | yes | Label of the primary outcome tracked for hourly odds. |
| `event_id` | `string` | yes | Parent event identifier. |
| `event_slug` | `string` | yes | Parent event slug. |
| `question` | `string` | yes | Market question text. |
| `market_slug` | `string` | yes | Market slug. |
| `description` | `string` | yes | Market description. |
| `outcomes` | `string` | yes | Serialized outcome labels for the market. |
| `condition_id` | `string` | yes | On-chain condition identifier. |
| `sports_market_type` | `string` | yes | Sports market type classification. |
| `group_item_title` | `string` | yes | Grouped market item title, when applicable. |
| `tags` | `string` | yes | Market tags. |
| `category` | `string` | yes | Market category. |

### Market status

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `is_active` | `bool` | yes | Whether the market is active. |
| `is_closed` | `bool` | yes | Whether the market is closed. |
| `is_resolved` | `bool` | yes | Whether the market is resolved. |
| `winning_outcome` | `string` | yes | Resolved winning outcome label, if resolved. |
| `winning_clob_token_id` | `string` | yes | CLOB token ID of the winning outcome, if resolved. |

### Market volume and timing

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `market_volume_usd` | `double` | yes | Lifetime market volume in USD. |
| `game_start_time` | `timestamp[us]` | yes | Scheduled game or event start time. |
| `end_time` | `timestamp[us]` | yes | Market end time. |
| `created_at` | `timestamp[us]` | yes | Market creation time. |

### Event metadata

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `event_title` | `string` | yes | Parent event title. |
| `event_description` | `string` | yes | Parent event description. |
| `event_start_at` | `timestamp[us]` | yes | Parent event start time. |
| `event_finished_at` | `timestamp[us]` | yes | Parent event finish time, if finished. |
| `event_volume_usd_lifetime_reported` | `double` | yes | Reported lifetime event volume in USD. |
| `volume_24h_usd` | `double` | yes | Event volume over the last 24 hours in USD. |
| `volume_1w_usd` | `double` | yes | Event volume over the last week in USD. |
| `volume_1m_usd` | `double` | yes | Event volume over the last month in USD. |
| `volume_1y_usd` | `double` | yes | Event volume over the last year in USD. |
| `event_liquidity_usd` | `double` | yes | Event liquidity in USD. |
| `event_is_active` | `bool` | yes | Whether the parent event is active. |
| `event_is_closed` | `bool` | yes | Whether the parent event is closed. |
| `event_tags` | `string` | yes | Parent event tags. |

### Hourly odds

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `odds_hour_utc` | `timestamp[us]` | yes | Hour bucket start as a UTC timestamp. |
| `open_odds` | `double` | yes | First observed odds in the hour. |
| `high_odds` | `double` | yes | Highest observed odds in the hour. |
| `low_odds` | `double` | yes | Lowest observed odds in the hour. |
| `close_odds` | `double` | yes | Last observed odds in the hour. |
| `avg_odds` | `double` | yes | Average observed odds in the hour. |

### Observation metadata

| Column | Type | Nullable | Description |
| --- | --- | --- | --- |
| `observed_points` | `int64` | yes | Number of odds observations aggregated into the hour. |
| `first_observed_at` | `timestamp[us]` | yes | Timestamp of the first observation in the hour. |
| `last_observed_at` | `timestamp[us]` | yes | Timestamp of the last observation in the hour. |

## Companion files

Each parquet export is paired with a machine-readable schema:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

Place parquet and schema files at the repository root or under `data/`. They are git-ignored because the parquet is large (~600 MB).
