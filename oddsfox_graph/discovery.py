"""Public discovery API.

Pipeline implementation details live under :mod:`oddsfox_graph._discovery`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._discovery.artifact_contracts import (
    DISCOVERY_PARQUET_ARTIFACTS,
    PARSE_ERROR_COLUMNS,
    PROPOSITION_COLUMNS,
    REJECTED_EDGE_COLUMNS,
)
from ._discovery.consensus import (
    MODEL_ASSESSMENT_COLUMNS,
    PARSE_ASSESSMENT_COLUMNS,
    QUARANTINE_COLUMNS,
)
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
from ._discovery.modes import policy_for
from ._discovery.workspace import CANDIDATE_COLUMNS

if TYPE_CHECKING:
    from ._discovery.cache import InferenceCache


def discover(
    input_path: Path,
    out_dir: Path,
    *,
    config: DiscoveryConfig | None = None,
    **test_dependencies: Any,
) -> dict[str, object]:
    """Dispatch discovery at the single public mode boundary."""

    active = config or DiscoveryConfig()
    policy = policy_for(active.mode)
    if not policy.semantic_enrichment:
        if test_dependencies:
            raise ValueError("Fast mode does not accept model test dependencies")
        from ._discovery.fast import discover_fast

        return discover_fast(input_path, out_dir, config=active)
    from ._discovery.pipeline import discover as discover_full

    return discover_full(
        input_path,
        out_dir,
        config=active,
        **test_dependencies,
    )


def __getattr__(name: str) -> object:
    if name == "InferenceCache":
        from ._discovery.cache import InferenceCache

        return InferenceCache
    raise AttributeError(name)

__all__ = [
    "CANDIDATE_COLUMNS",
    "DEFAULT_EMBEDDING_REVISION",
    "DISCOVERY_PARQUET_ARTIFACTS",
    "PARSE_ERROR_COLUMNS",
    "PROPOSITION_COLUMNS",
    "REJECTED_EDGE_COLUMNS",
    "MODEL_ASSESSMENT_COLUMNS",
    "PARSE_ASSESSMENT_COLUMNS",
    "QUARANTINE_COLUMNS",
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
