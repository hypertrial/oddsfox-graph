"""Tunable gates and methodology constants.

Values marked FALLBACK are overridden by empirical calibration when enough
ground-truth complement pairs exist in the same liquidity bucket.
"""

MIN_MARKET_VOLUME_USD = 10_000
MIN_ACTIVE_MINUTES = 1_000
MIN_OVERLAP_MINUTES = 1_000

# Price-only acceptance floors (FALLBACK when calibration bucket is sparse)
EQUIVALENCE_MEAN_ABS_DIFF_MAX = 0.02
EQUIVALENCE_CURRENT_ABS_DIFF_MAX = 0.03

IMPLICATION_EPSILON = 0.01
IMPLICATION_VIOLATION_MEAN_MAX = 0.005
IMPLICATION_CURRENT_SLACK = 0.02

EXCLUSION_EPSILON = 0.01
EXCLUSION_VIOLATION_MEAN_MAX = 0.005
EXCLUSION_CURRENT_SUM_MAX = 1.02

COMPLEMENT_LOW_OVERLAP_MINUTES = 10
COMPLEMENT_CURRENT_GAP_VIOLATION_MIN = 0.02  # FALLBACK absolute floor
COMPLEMENT_MEAN_GAP_VIOLATION_MIN = 0.01  # FALLBACK

# Time-weighted scoring
EW_HALF_LIFE_DAYS = 7
RECENT_WINDOW_HOURS = 24
# Minutes included in EW gap stats. Older data contributes <1% weight at 7-day
# half-life but multiplies pair-minute rows by ~12x on year-long feeds.
SCORING_LOOKBACK_DAYS = 30

# Noise model and violations
K_SIGMA = 3.0
VIOLATION_MIN_PERSISTENCE_MINUTES = 30
# Trailing window scanned when computing breach persistence. Kept well above
# VIOLATION_MIN_PERSISTENCE_MINUTES but far below full price history so the
# persistence row_number() only sorts a small recent slice per pair, not
# every aligned minute since market open.
PERSISTENCE_LOOKBACK_MINUTES = 180
MAX_CURRENT_SKEW_MINUTES = 5

# Calibration
CALIBRATION_QUANTILE = 0.95
MIN_CALIBRATION_SAMPLES = 10
NUM_LIQUIDITY_BUCKETS = 5

# Global coherence LP
LP_MAX_NODES_PER_EVENT = 500
LP_MAX_CONSTRAINTS_PER_EVENT = 2_000
LP_INCOHERENCE_THRESHOLD = 0.05
