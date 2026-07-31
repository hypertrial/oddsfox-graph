from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from .workspace import CandidateStore


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
    baseline_parse_entries_seeded: int
    baseline_embedding_vectors_available: int
    baseline_solver_components_available: int
    execution_plan: dict[str, object]
    embedding_vectors_reused: int
    embedding_vectors_recomputed: int
    semantic_neighborhoods_reused: int
    semantic_neighborhoods_recomputed: int
    semantic_neighborhoods_removed: int
    candidate_components_reused: int
    candidate_components_recomputed: int
    candidate_components_removed: int
    classifications_seeded: int
    baseline_classification_entries_seeded: int


@dataclass(frozen=True)
class IncrementalResources:
    prior_candidate_components: dict[str, str]
    prior_market_hashes: dict[str, str]
    prior_solver_hashes: frozenset[str]
    baseline_semantic_fingerprints: list[dict[str, Any]]
    baseline_embedding_state_path: Path | None
    baseline_semantic_state_path: Path | None
    prior_classifications: list[dict[str, Any]]
    prior_enabled_rules: frozenset[str]
    prior_propositions: list[dict[str, Any]]
    unchanged_market_ids: frozenset[str]
    reusable_candidates_path: Path | None
    baseline_candidate_blocks: Path | None
    baseline_candidate_reasons: Path | None

    @classmethod
    def empty(cls) -> IncrementalResources:
        return cls(
            prior_candidate_components={},
            prior_market_hashes={},
            prior_solver_hashes=frozenset(),
            baseline_semantic_fingerprints=[],
            baseline_embedding_state_path=None,
            baseline_semantic_state_path=None,
            prior_classifications=[],
            prior_enabled_rules=frozenset(),
            prior_propositions=[],
            unchanged_market_ids=frozenset(),
            reusable_candidates_path=None,
            baseline_candidate_blocks=None,
            baseline_candidate_reasons=None,
        )


@dataclass(frozen=True)
class IncrementalPreparation:
    reusable_solver_components: dict[str, dict[str, Any]]
    stats: IncrementalStats
    resources: IncrementalResources


@dataclass(frozen=True)
class ParsingStageResult:
    propositions: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


@dataclass(frozen=True)
class RetrievalStageResult:
    workspace: CandidateStore
    reused: bool


@dataclass(frozen=True)
class AdjudicationStageResult:
    edges: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


@dataclass(frozen=True)
class SolvingStageResult:
    accepted_edges: list[dict[str, Any]]
    rejected_edges: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    stats: dict[str, int]


@dataclass(frozen=True)
class PublicationStageResult:
    stats: dict[str, object]
