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
