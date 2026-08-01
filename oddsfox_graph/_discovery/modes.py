"""Mode policies for the shared discovery orchestration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FastModePolicy:
    mode: Literal["fast"] = "fast"
    validation_status: Literal["DETERMINISTIC_VALIDATED"] = (
        "DETERMINISTIC_VALIDATED"
    )
    semantic_enrichment: bool = False
    requires_inference: bool = False
    default_deadline_seconds: float = 120.0


@dataclass(frozen=True)
class FullModePolicy:
    mode: Literal["full"] = "full"
    validation_status: Literal["EXPERIMENTAL_FULL"] = "EXPERIMENTAL_FULL"
    semantic_enrichment: bool = True
    requires_inference: bool = True
    default_deadline_seconds: float = 3_600.0


ModePolicy = FastModePolicy | FullModePolicy


def policy_for(mode: Literal["fast", "full"]) -> ModePolicy:
    return FastModePolicy() if mode == "fast" else FullModePolicy()
