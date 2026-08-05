# OddsFox Graph

This repository was reset to a minimal skeleton for a WC2026 hourly-odds rebuild.

## Source data

The product input is the Pipeline golden mart export:

- `polymarket_wc2026_market_hourly_odds_<timestamp>.parquet`
- `polymarket_wc2026_market_hourly_odds_<timestamp>.schema.json`

Place these files at the repository root or under `data/`. They are git-ignored
because the parquet is large (~600 MB).

Grain: one row per `(market_id, odds_hour_epoch)` with primary-outcome hourly
OHLC and market/event metadata.

## Status

The previous logical-atlas discovery stack has been removed. Implementation will
be rebuilt on top of this hourly mart contract.
