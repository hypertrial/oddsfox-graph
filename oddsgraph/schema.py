"""Pydantic schemas for graph fragments and canonical graph artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from oddsgraph.ontology import EdgeType, NodeType


LLMNodeType = Literal["COMPETITION", "STAGE", "GROUP", "ROUND", "MATCH", "TEAM"]
LLMEdgeType = Literal["PART_OF", "PARTICIPATES_IN", "QUALIFIES_FOR", "ADVANCES_TO"]


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


class CompactNode(BaseModel):
    """Short-key wire format for LLM structured output (fewer decode tokens)."""

    id: str
    t: LLMNodeType
    l: str
    a: list[str] = Field(default_factory=list)
    c: float = Field(ge=0.0, le=1.0)
    e: list[str] = Field(min_length=1)

    def to_node(self) -> Node:
        return Node(
            local_id=self.id,
            type=NodeType(self.t),
            label=self.l,
            aliases=self.a,
            confidence=self.c,
            evidence_market_ids=self.e,
        )

    @classmethod
    def from_node(cls, node: Node) -> CompactNode:
        return cls(
            id=node.local_id,
            t=node.type.value,  # type: ignore[arg-type]
            l=node.label,
            a=list(node.aliases),
            c=node.confidence,
            e=list(node.evidence_market_ids),
        )


class CompactEdge(BaseModel):
    s: str
    d: str
    t: LLMEdgeType
    c: float = Field(ge=0.0, le=1.0)
    e: list[str] = Field(min_length=1)
    x: str = ""

    def to_edge(self) -> Edge:
        return Edge(
            source=self.s,
            target=self.d,
            type=EdgeType(self.t),
            confidence=self.c,
            evidence_market_ids=self.e,
            evidence_text=self.x,
        )

    @classmethod
    def from_edge(cls, edge: Edge) -> CompactEdge:
        return cls(
            s=edge.source,
            d=edge.target,
            t=edge.type.value,  # type: ignore[arg-type]
            c=edge.confidence,
            e=list(edge.evidence_market_ids),
            x=edge.evidence_text,
        )


class CompactGraphFragment(BaseModel):
    n: list[CompactNode] = Field(default_factory=list)
    g: list[CompactEdge] = Field(default_factory=list)

    def to_graph_fragment(self) -> GraphFragment:
        return GraphFragment(
            nodes=[node.to_node() for node in self.n],
            edges=[edge.to_edge() for edge in self.g],
        )

    @classmethod
    def from_graph_fragment(cls, fragment: GraphFragment) -> CompactGraphFragment:
        allowed_nodes = {
            "COMPETITION",
            "STAGE",
            "GROUP",
            "ROUND",
            "MATCH",
            "TEAM",
        }
        allowed_edges = {
            "PART_OF",
            "PARTICIPATES_IN",
            "QUALIFIES_FOR",
            "ADVANCES_TO",
        }
        nodes = [n for n in fragment.nodes if n.type.value in allowed_nodes]
        node_ids = {n.local_id for n in nodes}
        edges = [
            e
            for e in fragment.edges
            if e.type.value in allowed_edges
            and e.source in node_ids
            and e.target in node_ids
        ]
        return cls(
            n=[CompactNode.from_node(node) for node in nodes],
            g=[CompactEdge.from_edge(edge) for edge in edges],
        )


def merge_fragments(fragments: list[GraphFragment]) -> GraphFragment:
    """Merge fragment nodes/edges, unioning evidence and taking max confidence."""
    nodes_by_id: dict[str, Node] = {}
    edges_by_key: dict[tuple[str, str, str], Edge] = {}

    for fragment in fragments:
        for node in fragment.nodes:
            existing = nodes_by_id.get(node.local_id)
            if existing is None:
                nodes_by_id[node.local_id] = node
            else:
                merged_evidence = sorted(
                    set(existing.evidence_market_ids) | set(node.evidence_market_ids)
                )
                merged_aliases = sorted(set(existing.aliases) | set(node.aliases))
                nodes_by_id[node.local_id] = node.model_copy(
                    update={
                        "confidence": max(existing.confidence, node.confidence),
                        "evidence_market_ids": merged_evidence,
                        "aliases": merged_aliases,
                    }
                )
        for edge in fragment.edges:
            key = (edge.source, edge.target, edge.type.value)
            existing = edges_by_key.get(key)
            if existing is None:
                edges_by_key[key] = edge
                continue
            primary = existing if existing.confidence >= edge.confidence else edge
            secondary = edge if primary is existing else existing
            merged_evidence = sorted(
                set(existing.evidence_market_ids) | set(edge.evidence_market_ids)
            )
            edges_by_key[key] = primary.model_copy(
                update={
                    "confidence": max(existing.confidence, edge.confidence),
                    "evidence_market_ids": merged_evidence,
                    "evidence_text": primary.evidence_text or secondary.evidence_text,
                }
            )

    return GraphFragment(
        nodes=list(nodes_by_id.values()),
        edges=list(edges_by_key.values()),
    )


class Proposition(BaseModel):
    """Formal truth condition attached to an OUTCOME node."""

    predicate: str
    arguments: dict[str, str] = Field(default_factory=dict)
    polarity: bool = True
    comparator: str | None = None
    value: float | None = None
    unit: str | None = None
    time_start: datetime | None = None
    time_end: datetime | None = None

    def key(self) -> str:
        """Stable string form for dedup, rule grouping, and edge premises."""
        args = ",".join(f"{k}={self.arguments[k]}" for k in sorted(self.arguments))
        polarity = "" if self.polarity else "!"
        base = f"{polarity}{self.predicate}({args})"
        extras: list[str] = []
        if self.comparator is not None:
            extras.append(f"cmp={self.comparator}")
        if self.value is not None:
            extras.append(f"val={self.value}")
        if self.unit is not None:
            extras.append(f"unit={self.unit}")
        if self.time_start is not None:
            extras.append(f"start={self.time_start.isoformat()}")
        if self.time_end is not None:
            extras.append(f"end={self.time_end.isoformat()}")
        if extras:
            return f"{base}|{'|'.join(extras)}"
        return base


class CanonicalNode(BaseModel):
    canonical_id: str
    type: NodeType
    label: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(default_factory=list)
    resolution_method: str = "unresolved"
    inference_method: str = "unknown"
    proposition: Proposition | None = None


class CanonicalEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_market_ids: list[str] = Field(min_length=1)
    evidence_text: str = ""
    inference_method: str = "unknown"
    derivation_type: str = "extraction"
    rule_id: str | None = None
    rule_version: int | None = None
    premises: list[str] | None = None


class RejectedEdge(CanonicalEdge):
    rejection_reason: str


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
    events_deterministic: int = 0
    events_deterministic_verified: int = 0
    events_deterministic_corrected: int = 0
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    resolution_tiers: dict[str, int] = Field(default_factory=dict)
    rejected_edge_reasons: dict[str, int] = Field(default_factory=dict)
    per_event_status: dict[str, str] = Field(default_factory=dict)
