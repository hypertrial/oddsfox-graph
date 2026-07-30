from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, NotRequired, TypedDict, TypeVar


class CandidateRow(TypedDict):
    proposition_a_id: str
    proposition_b_id: str
    candidate_reasons: list[str]
    embedding_similarity: float | None
    embedding_rank: int | None
    deterministic_relation: str | None
    rule_id: str | None
    rule_status: str | None
    classification_relation: str | None
    classification_confidence: float | None
    supporting_fields: str | None
    a_implies_b: bool | None
    b_implies_a: bool | None
    explanation: str | None
    assumptions: list[str]
    requires_review: bool
    status: str
    discovery_method: str | None
    model_version: str | None
    prompt_version: str | None
    _deterministic: NotRequired[dict[str, object]]


class IncrementalStats(TypedDict, total=False):
    enabled: bool
    offline_state_replay: bool
    baseline_manifest_hash: str | None
    markets_reused: int
    markets_changed: int
    markets_removed: int
    candidate_generation_reused: bool
    candidate_blocks_reused: int
    candidate_blocks_recomputed: int
    candidate_blocks_removed: int
    affected_only_verified: bool
    invalidation_reasons: list[str]


T = TypeVar("T")


@dataclass(frozen=True)
class StageResult(Generic[T]):
    value: T
    counts: dict[str, int]
