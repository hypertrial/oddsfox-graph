from __future__ import annotations

from . import thresholds as T
from .thresholds import ThresholdBucketCounts


def ew_lambda_sql() -> str:
    half_life_seconds = T.EW_HALF_LIFE_DAYS * 24 * 3600
    return f"ln(2) / {half_life_seconds}"


def _gap_exprs() -> tuple[str, str]:
    gap = f"""
        CASE p.candidate_type
            WHEN 'complement' THEN abs(a.scoring_price + b.scoring_price - 1)
            WHEN 'equivalence' THEN abs(a.scoring_price - b.scoring_price)
            WHEN 'implication' THEN greatest(
                0, a.scoring_price - b.scoring_price - {T.IMPLICATION_EPSILON})
            WHEN 'mutual_exclusion' THEN greatest(
                0, a.scoring_price + b.scoring_price - 1 - {T.EXCLUSION_EPSILON})
            ELSE abs(a.scoring_price - b.scoring_price)
        END
    """
    raw_gap = f"""
        CASE p.candidate_type
            WHEN 'complement' THEN abs(a.scoring_price + b.scoring_price - 1)
            WHEN 'equivalence' THEN abs(a.scoring_price - b.scoring_price)
            WHEN 'implication' THEN greatest(0, a.scoring_price - b.scoring_price)
            WHEN 'mutual_exclusion' THEN greatest(0, a.scoring_price + b.scoring_price - 1)
            ELSE abs(a.scoring_price - b.scoring_price)
        END
    """
    return gap, raw_gap


def create_quote_views_sql(quotes_path: str | None) -> str:
    if not quotes_path:
        return """
            CREATE TABLE quote_minute_prices AS
            SELECT
                NULL::VARCHAR AS clob_token_id,
                NULL::BIGINT AS odds_minute_epoch,
                NULL::DOUBLE AS mid_price,
                NULL::DOUBLE AS half_spread
            WHERE false;
        """
    return f"""
        CREATE TABLE quote_minute_prices AS
        SELECT
            clob_token_id,
            CAST(floor(odds_timestamp_epoch / 60) * 60 AS BIGINT) AS odds_minute_epoch,
            (bid + ask) / 2.0 AS mid_price,
            greatest((ask - bid) / 2.0, 0.0) AS half_spread
        FROM (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY clob_token_id,
                        CAST(floor(odds_timestamp_epoch / 60) * 60 AS BIGINT)
                    ORDER BY odds_timestamp_epoch DESC
                ) AS rn
            FROM read_parquet('{quotes_path}')
        )
        WHERE rn = 1;
    """


def create_enriched_minute_prices_sql() -> str:
    return """
        CREATE TABLE enriched_minute_prices AS
        WITH market_sums AS (
            SELECT market_id, odds_minute_epoch, sum(price) AS market_minute_sum
            FROM token_minute_prices
            GROUP BY 1, 2
        ),
        rolling_noise AS (
            SELECT
                clob_token_id,
                odds_minute_epoch,
                greatest(
                    0.001,
                    stddev_samp(price) OVER (
                        PARTITION BY clob_token_id
                        ORDER BY odds_minute_epoch
                        ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                    )
                ) AS rolling_sigma
            FROM token_minute_prices
        )
        SELECT
            p.*,
            m.market_minute_sum,
            CASE
                WHEN m.market_minute_sum > 0 THEN p.price / m.market_minute_sum
                ELSE p.price
            END AS price_devig,
            coalesce(q.mid_price, p.price) AS scoring_price,
            coalesce(q.half_spread, r.rolling_sigma, 0.001) AS noise_floor
        FROM token_minute_prices p
        JOIN market_sums m
            ON p.market_id = m.market_id
            AND p.odds_minute_epoch = m.odds_minute_epoch
        LEFT JOIN quote_minute_prices q
            ON p.clob_token_id = q.clob_token_id
            AND p.odds_minute_epoch = q.odds_minute_epoch
        LEFT JOIN rolling_noise r
            ON p.clob_token_id = r.clob_token_id
            AND p.odds_minute_epoch = r.odds_minute_epoch;
    """


def create_scoring_minute_prices_sql(lookback_days: int | None = None) -> str:
    lookback = (lookback_days or T.SCORING_LOOKBACK_DAYS) * 24 * 3600
    return f"""
        CREATE TABLE scoring_minute_prices AS
        SELECT *
        FROM enriched_minute_prices
        WHERE odds_minute_epoch >= (
            SELECT max(odds_minute_epoch) - {lookback}
            FROM enriched_minute_prices
        );
    """


def create_aligned_edges_sql() -> str:
    lam = ew_lambda_sql()
    gap, _raw_gap = _gap_exprs()
    recent_cutoff = T.RECENT_WINDOW_HOURS * 3600
    return f"""
        CREATE TABLE aligned_edges AS
        WITH pairs AS (
            SELECT DISTINCT src_node_id, dst_node_id, candidate_type
            FROM candidate_edges_v
        ),
        bounds AS (
            SELECT max(odds_minute_epoch) AS max_epoch
            FROM scoring_minute_prices
        ),
        aligned AS (
            SELECT
                p.src_node_id,
                p.dst_node_id,
                p.candidate_type,
                a.odds_minute_epoch,
                a.scoring_price AS p_src,
                b.scoring_price AS p_dst,
                a.noise_floor AS noise_src,
                b.noise_floor AS noise_dst,
                exp(-({lam}) * (bounds.max_epoch - a.odds_minute_epoch)) AS ew_weight,
                {gap} AS gap
            FROM pairs p
            JOIN scoring_minute_prices a ON a.clob_token_id = p.src_node_id
            JOIN scoring_minute_prices b
                ON b.clob_token_id = p.dst_node_id
                AND b.odds_minute_epoch = a.odds_minute_epoch
            CROSS JOIN bounds
        ),
        stats AS (
            SELECT
                src_node_id,
                dst_node_id,
                candidate_type,
                count(*) AS overlap_minutes,
                avg(p_src) AS mean_p_src,
                avg(p_dst) AS mean_p_dst,
                avg(gap) AS gap_mean_raw,
                sum(gap * ew_weight) / nullif(sum(ew_weight), 0) AS gap_ew_mean,
                max(CASE
                    WHEN odds_minute_epoch >= (SELECT max_epoch FROM bounds) - {recent_cutoff}
                    THEN gap
                END) AS gap_recent_max,
                stddev_samp(gap) AS gap_sigma,
                avg(noise_src + noise_dst) AS pair_noise_floor,
                max(odds_minute_epoch) AS pair_max_epoch
            FROM aligned
            GROUP BY 1, 2, 3
        )
        SELECT
            p.src_node_id,
            p.dst_node_id,
            p.candidate_type,
            coalesce(s.overlap_minutes, 0) AS overlap_minutes,
            s.mean_p_src,
            s.mean_p_dst,
            CASE WHEN p.candidate_type = 'complement' THEN s.gap_ew_mean END AS complement_error,
            CASE WHEN p.candidate_type = 'equivalence' THEN s.gap_ew_mean END AS equivalence_error,
            CASE WHEN p.candidate_type = 'implication' THEN s.gap_ew_mean END AS implication_violation,
            CASE WHEN p.candidate_type = 'mutual_exclusion' THEN s.gap_ew_mean END AS exclusion_violation,
            CASE WHEN p.candidate_type = 'complement' THEN s.gap_mean_raw END AS complement_error_raw,
            CASE WHEN p.candidate_type = 'equivalence' THEN s.gap_mean_raw END AS equivalence_error_raw,
            CASE WHEN p.candidate_type = 'implication' THEN s.gap_mean_raw END AS implication_violation_raw,
            CASE WHEN p.candidate_type = 'mutual_exclusion' THEN s.gap_mean_raw END AS exclusion_violation_raw,
            s.gap_sigma,
            s.pair_noise_floor,
            s.gap_recent_max,
            s.pair_max_epoch
        FROM candidate_edges_v p
        LEFT JOIN stats s USING (src_node_id, dst_node_id, candidate_type);
    """


def create_pair_persistence_sql(threshold_bucket_counts: ThresholdBucketCounts) -> str:
    _, raw_gap = _gap_exprs()
    lookback_seconds = threshold_bucket_counts.persistence_lookback_seconds
    persistence_buckets = threshold_bucket_counts.violation_persistence_buckets
    return f"""
        CREATE TABLE pair_persistence AS
        WITH pairs AS (
            SELECT DISTINCT src_node_id, dst_node_id, candidate_type
            FROM candidate_edges_v
        ),
        recent AS (
            SELECT
                p.src_node_id,
                p.dst_node_id,
                p.candidate_type,
                a.odds_minute_epoch,
                a.odds_timestamp,
                {raw_gap} AS raw_gap,
                a.noise_floor AS noise_src,
                b.noise_floor AS noise_dst
            FROM pairs p
            JOIN aligned_edges e USING (src_node_id, dst_node_id, candidate_type)
            JOIN scoring_minute_prices a ON a.clob_token_id = p.src_node_id
            JOIN scoring_minute_prices b
                ON b.clob_token_id = p.dst_node_id
                AND b.odds_minute_epoch = a.odds_minute_epoch
            WHERE e.pair_max_epoch IS NOT NULL
              AND a.odds_minute_epoch >= e.pair_max_epoch - {lookback_seconds}
        ),
        flagged AS (
            SELECT
                src_node_id,
                dst_node_id,
                candidate_type,
                odds_minute_epoch,
                odds_timestamp,
                raw_gap,
                CASE candidate_type
                    WHEN 'complement' THEN raw_gap >= greatest(
                        {T.COMPLEMENT_CURRENT_GAP_VIOLATION_MIN},
                        {T.K_SIGMA} * coalesce(noise_src + noise_dst, 0.01))
                    WHEN 'equivalence' THEN raw_gap > greatest(
                        {T.EQUIVALENCE_CURRENT_ABS_DIFF_MAX},
                        {T.K_SIGMA} * coalesce(noise_src + noise_dst, 0.01))
                    WHEN 'implication' THEN raw_gap > greatest(
                        {T.IMPLICATION_CURRENT_SLACK},
                        {T.K_SIGMA} * coalesce(noise_src + noise_dst, 0.01))
                    WHEN 'mutual_exclusion' THEN raw_gap > greatest(
                        {T.EXCLUSION_CURRENT_SUM_MAX - 1.0},
                        {T.K_SIGMA} * coalesce(noise_src + noise_dst, 0.01))
                    ELSE false
                END AS in_breach
            FROM recent
        ),
        ordered AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY src_node_id, dst_node_id, candidate_type
                    ORDER BY odds_minute_epoch DESC
                ) AS rn_desc
            FROM flagged
        ),
        trail_stats AS (
            SELECT
                src_node_id,
                dst_node_id,
                candidate_type,
                count(*) FILTER (
                    WHERE in_breach AND rn_desc <= {persistence_buckets}
                ) AS trailing_breach_minutes,
                min(odds_timestamp) FILTER (
                    WHERE in_breach AND rn_desc <= {persistence_buckets}
                ) AS first_seen_ts,
                max(odds_timestamp) FILTER (
                    WHERE in_breach AND rn_desc <= {persistence_buckets}
                ) AS last_seen_ts,
                count(*) FILTER (WHERE rn_desc <= {persistence_buckets}) AS trailing_window_minutes,
                count(*) FILTER (
                    WHERE in_breach AND rn_desc <= {persistence_buckets}
                )::DOUBLE / greatest(
                    count(*) FILTER (WHERE rn_desc <= {persistence_buckets}), 1
                ) AS breach_fraction_recent
            FROM ordered
            WHERE rn_desc <= {persistence_buckets}
            GROUP BY 1, 2, 3
        )
        SELECT * FROM trail_stats;
    """
