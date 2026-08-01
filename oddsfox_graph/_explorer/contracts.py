"""Typed contracts shared by the Python API, HTTP API, and frontend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ExplorerLevel = Literal["component", "event", "proposition"]
LayoutMode = Literal["hierarchical", "close_time"]
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
CoverageStatus = Literal["not_applicable", "not_started", "partial", "complete"]
EdgeMode = Literal["essential", "all"]


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
    classification_coverage: float | None = None
    classification_status: CoverageStatus = "not_applicable"
    progression_outcome: bool | None = None
    market_close_epoch: int | None = None


class ExplorerEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    source: str
    target: str
    relation: ExplorerRelation
    count: int = 1
    confidence: float
    discovery_method: str
    evidence_tier: EvidenceTier
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
    edge_mode: EdgeMode = "all"
    layout_mode: LayoutMode = "hierarchical"
    display_stats: GraphDisplayStats | None = None


class GraphDisplayStats(BaseModel):
    """Counts explaining what a human view includes or intentionally omits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_node_count: int = Field(ge=0)
    input_edge_count: int = Field(ge=0)
    display_node_count: int = Field(ge=0)
    display_edge_count: int = Field(ge=0)
    omitted_edge_count: int = Field(ge=0)
    density: float = Field(ge=0.0, le=1.0)
    label_uniqueness: float = Field(ge=0.0, le=1.0)
    max_degree: int = Field(ge=0)
    recommended_representation: Literal["network", "grouped"]


class TournamentScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["oddsfox-pipeline"] = "oddsfox-pipeline"
    scope: Literal["wc2026"] = "wc2026"
    universe: Literal["knockout_progression"] = "knockout_progression"
    selection: Literal["all_valid_pipeline_wc2026_markets"] = (
        "all_valid_pipeline_wc2026_markets"
    )
    input_hourly_rows: int = Field(default=0, ge=0)
    market_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    team_count: int = Field(ge=0)
    stage_count: int = Field(ge=0)
    first_odds_hour_epoch: int | None = None
    last_odds_hour_epoch: int | None = None
    adapter_version: str = "polymarket-wc2026-graph-hourly-v1"
    truncated: Literal[False] = False


class StageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_key: str
    label: str
    stage_rank: int = Field(ge=0, le=5)
    normalized_progression_level: int = Field(ge=0, le=5)
    team_count: int = Field(ge=0)
    market_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    active_market_count: int = Field(ge=0)
    closed_market_count: int = Field(ge=0)
    classification_eligible_count: int = Field(ge=0)
    classification_assessed_count: int = Field(ge=0)
    classification_status: CoverageStatus
    classification_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class TeamSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    team_key: str
    canonical_team_name: str
    is_still_alive: bool | None = None
    market_status: str | None = None
    market_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    stage_keys: tuple[str, ...]
    min_stage_rank: int = Field(ge=0, le=5)
    max_stage_rank: int = Field(ge=0, le=5)
    classification_eligible_count: int = Field(ge=0)
    classification_assessed_count: int = Field(ge=0)
    classification_status: CoverageStatus
    classification_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class ClaimSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    market_id: str
    canonical_team_name: str
    stage_key: str
    stage_rank: int = Field(ge=0, le=5)
    normalized_progression_level: int = Field(ge=0, le=5)
    question: str
    answer: Literal["Yes", "No"]
    plain_claim: str
    is_progression_token: bool
    market_status: str
    is_still_alive: bool | None = None
    market_close_epoch: int | None = None
    technical_canonical_label: str


class MarketDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: str
    event_slug: str
    question: str
    canonical_team_name: str
    stage_key: str
    stage_rank: int = Field(ge=0, le=5)
    normalized_progression_level: int = Field(ge=0, le=5)
    market_direction: Literal["winner", "advance", "elimination"]
    market_status: str
    is_still_alive: bool | None = None
    market_close_epoch: int | None = None
    claims: tuple[ClaimSummary, ...]


class RelationshipDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    source: ClaimSummary
    target: ClaimSummary
    relation: ExplorerRelation
    basis: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_tier: EvidenceTier
    discovery_method: str
    explanation: str


class RelationshipGroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    description: str
    relation: ExplorerRelation
    member_claim_ids: tuple[str, ...]
    relationship_count: int = Field(ge=0)


class HumanHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    relationship: RelationshipDetail


class ExplorerCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["live", "static"]
    hierarchy: bool = True
    search: bool = True
    relationship_inspection: bool = True
    analyst_graph: bool = True
    compare: bool = True
    proof: bool
    why_not: bool
    recording: bool
    regeneration: bool


class StageDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: StageSummary
    teams: tuple[TeamSummary, ...]
    markets: tuple[MarketDetail, ...]


class TeamDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: TeamSummary
    markets: tuple[MarketDetail, ...]


class EntitySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["team", "stage", "market", "claim"]
    id: str
    label: str
    description: str


class CompareResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["same_claim", "direct", "path", "no_proven_relationship"]
    source: ClaimSummary
    target: ClaimSummary
    direct: RelationshipDetail | None = None
    path: tuple[RelationshipDetail, ...] = ()
    explanation: str


class ExploreHome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: TournamentScope
    stages: tuple[StageSummary, ...]
    teams: tuple[TeamSummary, ...]
    notable_relationships: tuple[HumanHighlight, ...]
    relationship_groups: tuple[RelationshipGroupSummary, ...]
    capabilities: ExplorerCapabilities
    display_stats: GraphDisplayStats
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
    stage_importance: float
    structural_reach: float
    template_novelty: float
    evidence_interest: float
    relation_interest: float
    confidence_contribution: float
    stage_importance_contribution: float
    structural_reach_contribution: float
    template_novelty_contribution: float
    evidence_interest_contribution: float
    relation_interest_contribution: float
    base_importance: float
    same_relation_count: int
    same_evidence_tier_count: int
    same_target_stage_count: int
    same_component_count: int
    same_relation_penalty: float
    same_evidence_tier_penalty: float
    same_target_stage_penalty: float
    same_component_penalty: float
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
    source_team_name: str
    source_stage_key: str
    source_stage_rank: int
    source_plain_claim: str
    target_id: str
    target_label: str
    target_market_id: str
    target_event_key: str
    target_domain: str
    target_team_name: str
    target_stage_key: str
    target_stage_rank: int
    target_plain_claim: str
    template_key: str
    component_id: str
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

    schema_version: Literal["oddsfox-recording-plan-v2"] = (
        "oddsfox-recording-plan-v2"
    )
    ranking_version: Literal["human-wc2026-story-edge-v2"] = (
        "human-wc2026-story-edge-v2"
    )
    graph_fingerprint: str
    mode: Literal["fast", "full"]
    validation_status: str
    requested_limit: int
    min_confidence: float
    eligible_edge_count: int
    candidate_pool_size: int
    excluded_missing_context: int = 0
    excluded_pathological: int = 0
    highlights: tuple[RecordingHighlight, ...]
    graph: GraphView
    context_pruning: RecordingContextPruning
