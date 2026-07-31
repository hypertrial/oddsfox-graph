"""Public discovery API.

Pipeline implementation details live under :mod:`oddsfox_graph._discovery`.
"""

from __future__ import annotations

from ._discovery.cache import InferenceCache
from ._discovery.contracts import (
    DEFAULT_EMBEDDING_REVISION,
    AtomicPairAssessment,
    DiscoveryConfig,
    ParsedMarket,
    ParsedOutcome,
    PropositionRecord,
    SourceMarket,
    SourceOutcome,
)
from ._discovery.pipeline import (
    DISCOVERY_PARQUET_ARTIFACTS,
    PARSE_ERROR_COLUMNS,
    PROPOSITION_COLUMNS,
    REJECTED_EDGE_COLUMNS,
    REVIEW_COLUMNS,
    discover,
)
from ._discovery.workspace import CANDIDATE_COLUMNS

__all__ = [
    "CANDIDATE_COLUMNS",
    "DEFAULT_EMBEDDING_REVISION",
    "DISCOVERY_PARQUET_ARTIFACTS",
    "PARSE_ERROR_COLUMNS",
    "PROPOSITION_COLUMNS",
    "REJECTED_EDGE_COLUMNS",
    "REVIEW_COLUMNS",
    "AtomicPairAssessment",
    "DiscoveryConfig",
    "InferenceCache",
    "ParsedMarket",
    "ParsedOutcome",
    "PropositionRecord",
    "SourceMarket",
    "SourceOutcome",
    "discover",
]
