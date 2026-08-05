"""Pydantic schemas for graph fragments and canonical graph artifacts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from oddsfox_graph.ontology import EdgeType, NodeType


class Node(BaseModel):
    local_id: str
    type: NodeType
    label: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(min_length=1)


class Edge(BaseModel):
    source: str
    target: str
    type: EdgeType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(min_length=1)
    evidence_text: str = ""

    @field_validator("evidence_text")
    @classmethod
    def strip_evidence_text(cls, v: str) -> str:
        return v.strip()


class GraphFragment(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class CanonicalNode(BaseModel):
    canonical_id: str
    type: NodeType
    label: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(default_factory=list)
    resolution_method: str = "unresolved"
    inference_method: str = "unknown"


class CanonicalEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(min_length=1)
    evidence_text: str = ""
    inference_method: str = "unknown"


class RejectedEdge(CanonicalEdge):
    rejection_reason: str


class UnresolvedEntity(BaseModel):
    local_id: str
    type: NodeType
    label: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float
    evidence_market_ids: list[str] = Field(default_factory=list)
    inference_method: str = "unknown"
    reason: str = "no_match"


class SemanticMarket(BaseModel):
    market_id: str
    event_id: str
    event_slug: str | None = None
    event_title: str | None = None
    event_description: str | None = None
    question: str | None = None
    description: str | None = None
    market_slug: str | None = None
    sports_market_type: str | None = None
    group_item_title: str | None = None
    outcomes: list[str] | None = None
    tags: list[str] | None = None
    event_tags: list[str] | None = None
    game_start_time: Any = None
    end_time: Any = None


class InferenceReport(BaseModel):
    model_path: str | None = None
    events_processed: int = 0
    events_failed: int = 0
    events_skipped: int = 0
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    resolution_tiers: dict[str, int] = Field(default_factory=dict)
    rejected_edge_reasons: dict[str, int] = Field(default_factory=dict)
    unresolved_count: int = 0
    per_event_status: dict[str, str] = Field(default_factory=dict)
