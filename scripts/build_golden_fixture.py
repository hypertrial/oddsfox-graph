"""One-off script to build golden fixture from real parquet."""

import duckdb

con = duckdb.connect()
con.execute(
    """
    COPY (
      SELECT DISTINCT
        market_id, event_id, event_slug, event_title, event_description,
        question, description, market_slug, sports_market_type, group_item_title,
        outcomes, tags, event_tags, game_start_time, end_time
      FROM read_parquet('polymarket_wc2026_market_hourly_odds_20260805T183112Z.parquet')
      WHERE (
        sports_market_type = 'moneyline' AND event_slug = 'fifwc-tur-par-2026-06-19'
      ) OR (
        sports_market_type = 'moneyline' AND event_slug = 'fifwc-esp-ksa-2026-06-21'
      ) OR (
        sports_market_type = 'soccer_team_to_advance' AND event_id = '351746'
      ) OR (
        event_slug = 'world-cup-group-d-winner'
      ) OR (
        event_slug = 'world-cup-golden-boot-winner'
      ) OR (
        event_slug = 'who-will-perform-at-world-cup-halftime-show'
      ) OR (
        sports_market_type = 'soccer_team_to_advance' AND event_slug = 'fifwc-bra-nor-2026-07-05-more-markets'
      )
    ) TO 'tests/fixtures/golden_semantic_markets.parquet' (FORMAT PARQUET)
    """
)
con.close()
print("golden fixture written")
