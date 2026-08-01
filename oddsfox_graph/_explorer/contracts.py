"""Typed contracts shared by the Python API, HTTP API, and frontend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ExplorerLevel = Literal["component", "event", "proposition"]
ExplorerRelation = Literal[
    "compatible",
    "complement",
    "equivalent",
    "implies",
    "mutually_exclusive",
]
EvidenceTier = Literal[
    "source_contract", "deterministic_rule", "generative_consensus"
]


class GraphFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domains: tuple[str, ...] = ()
    relations: tuple[ExplorerRelation, ...] = ()
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    discovery_methods: tuple[
        Literal["deterministic", "generative_consensus"], ...
    ] = ()
    evidence_tiers: tuple[EvidenceTier, ...] = ()
    active_only: bool = False
    closed_only: bool = False
    include_compatible: bool = False


class ExplorerNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    level: ExplorerLevel
    parent_id: str | None = None
    x: float
    y: float
    size: float
    domain: str | None = None
    component_id: str | None = None
    market_id: str | None = None
    proposition_count: int = 1
    edge_count: int = 0
    classification_coverage: float = 1.0


class ExplorerEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    source: str
    target: str
    relation: ExplorerRelation
    count: int = 1
    confidence: float
    discovery_method: str
    evidence_tier: str
    aggregation_only: bool = False


class GraphPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: tuple[dict[str, object], ...]
    next_cursor: str | None = None
    truncated: bool = False


class GraphView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: ExplorerLevel
    nodes: tuple[ExplorerNode, ...]
    edges: tuple[ExplorerEdge, ...]
    truncated_nodes: bool = False
    truncated_edges: bool = False
    coverage: dict[str, object]


class ExplorerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_version: str
    viewer: dict[str, object]
    coverage: dict[str, object]
    build: dict[str, object]


class RecordingScoreBreakdown(BaseModel):
    """Auditable feature and diversity values for one selected edge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confidence: float
    scope: float
    structural_reach: float
    evidence_interest: float
    relation_interest: float
    confidence_contribution: float
    scope_contribution: float
    structural_reach_contribution: float
    evidence_interest_contribution: float
    relation_interest_contribution: float
    base_importance: float
    same_relation_count: int
    same_evidence_tier_count: int
    same_event_pair_count: int
    shared_endpoint_count: int
    same_relation_penalty: float
    same_evidence_tier_penalty: float
    same_event_pair_penalty: float
    shared_endpoint_penalty: float
    total_penalty: float
    selection_score: float


class RecordingHighlight(BaseModel):
    """One ranked logical edge and the human-facing endpoint context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int
    proposal_id: str
    source_id: str
    source_label: str
    source_market_id: str
    source_event_key: str
    source_domain: str
    target_id: str
    target_label: str
    target_market_id: str
    target_event_key: str
    target_domain: str
    relation: ExplorerRelation
    confidence: float
    evidence_tier: EvidenceTier
    discovery_method: str
    explanation_excerpt: str
    importance_score: float
    score_breakdown: RecordingScoreBreakdown


class RecordingContextPruning(BaseModel):
    """Counts proving selected edges survived bounded context construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_edge_cap_per_endpoint: int
    candidate_nodes: int
    candidate_edges: int
    retained_nodes: int
    retained_edges: int
    pruned_nodes: int
    pruned_edges: int


class RecordingPlan(BaseModel):
    """Fingerprint-bound deterministic inputs for preview and recording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["oddsfox-recording-plan-v1"] = (
        "oddsfox-recording-plan-v1"
    )
    ranking_version: Literal["balanced-logic-edge-v1"] = (
        "balanced-logic-edge-v1"
    )
    graph_fingerprint: str
    mode: Literal["fast", "full"]
    validation_status: str
    requested_limit: int
    min_confidence: float
    eligible_edge_count: int
    candidate_pool_size: int
    highlights: tuple[RecordingHighlight, ...]
    graph: GraphView
    context_pruning: RecordingContextPruning
