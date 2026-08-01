"""Typed read-only API for a completed OddsFox graph."""

from __future__ import annotations

import json
import heapq
import itertools
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import __version__
from ._explorer.contracts import (
    ExplorerMetadata,
    GraphFilter,
    GraphPage,
    GraphView,
    RecordingPlan,
)
from ._explorer.queries import ExplorerStore
from .queries import DuckDB
from .search import PATH_SENTINEL, read_rows, require_artifact, resolve_node, search_nodes


Relation = Literal[
    "compatible",
    "complement",
    "equivalent",
    "implies",
    "mutually_exclusive",
]

_MAX_PROOF_HOPS = 8
_MAX_PROOF_PATHS = 20
_MAX_PROOF_OUTGOING = 10_000
_MAX_PROOF_STATES = 100_000


class Node(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    node_id: str
    market_id: str
    outcome_label: str
    event_slug: str
    canonical_proposition: str


class Edge(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Relation
    edge_basis: str
    confidence: float
    evidence: str
    discovery_method: Literal["deterministic", "generative_consensus"]


class ProofStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Literal["implies", "equivalent"]
    confidence: float
    proposal_id: str


class Proof(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node_id: str
    to_node_id: str
    steps: tuple[ProofStep, ...]
    hops: int
    bottleneck_confidence: float


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    status: Literal[
        "accepted",
        "solver_rejected",
        "quarantined_parse",
        "model_disagreement",
        "assumption",
        "invalid_citation",
        "nli_veto",
        "below_threshold",
        "inference_failure",
        "not_applicable_to_deterministic_rules",
        "full_mode_not_run",
        "deadline_budget_exhausted",
        "not_retrieved",
        "unknown_node",
    ]
    proposition_a_id: str | None = None
    proposition_b_id: str | None = None
    relation: str | None = None
    explanation: str


class Graph:
    """A manifest-complete, immutable graph output directory."""

    def __init__(self, out_dir: Path, *, build_mode: Literal["fast", "full"]) -> None:
        self.out_dir = out_dir
        self.build_mode = build_mode

    @classmethod
    def open(cls, out_dir: str | Path) -> Graph:
        resolved = Path(out_dir).resolve()
        manifest_path = resolved / "build_manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Graph output is incomplete: missing {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Graph manifest is invalid: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Graph manifest must be a JSON object")
        if manifest.get("command") != "discover":
            raise ValueError("Graph manifest is not a discovery output")
        if manifest.get("version") != __version__:
            raise ValueError(
                f"Graph output is incompatible; run a clean v{__version__} discovery"
            )
        build_mode = manifest.get("build_mode")
        if build_mode not in {"fast", "full"}:
            raise ValueError("Graph manifest has no valid v0.11 build mode")
        required = (
            "oddsfox_graph.duckdb",
            "nodes.parquet",
            "logic_edges.parquet",
            "conditional_edges.parquet",
            "relation_candidates.parquet",
            "rejected_edges.parquet",
            "quarantined_pairs.parquet",
            "event_summary.parquet",
            "event_relation_summary.parquet",
            "component_summary.parquet",
            "node_metrics.parquet",
            "visualization_layout.parquet",
            "coverage_summary.json",
            "viewer_manifest.json",
        )
        declared = manifest.get("artifacts")
        if not isinstance(declared, list) or any(
            artifact not in declared for artifact in required
        ):
            raise ValueError("Graph manifest does not declare the query artifacts")
        for artifact in required:
            require_artifact(resolved, artifact)
        return cls(resolved, build_mode=build_mode)

    def metadata(self) -> ExplorerMetadata:
        return self._explorer().metadata()

    def coverage(self) -> dict[str, object]:
        return self._explorer().coverage()

    def events(
        self,
        filters: GraphFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        return self._explorer().events(filters, cursor=cursor, limit=limit)

    def components(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        return self._explorer().components(cursor=cursor, limit=limit)

    def overview(
        self,
        level: Literal["component", "event"] = "event",
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self._explorer().overview(
            level,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def neighborhood(
        self,
        node_ids: tuple[str, ...],
        *,
        hops: int = 1,
        filters: GraphFilter | None = None,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self._explorer().neighborhood(
            node_ids,
            hops=hops,
            filters=filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def subgraph(
        self,
        node_ids: tuple[str, ...],
        *,
        hops: int = 1,
        filters: GraphFilter | None = None,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self.neighborhood(
            node_ids,
            hops=hops,
            filters=filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def recording_plan(
        self,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> RecordingPlan:
        """Build a deterministic, balanced recording plan for this graph."""

        return self._explorer().recording_plan(
            limit=limit,
            min_confidence=min_confidence,
        )

    def event(self, event_key: str) -> dict[str, object]:
        return self._explorer().event(event_key)

    def event_graph(
        self,
        event_key: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self._explorer().event_graph(
            event_key,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def component(self, component_id: str) -> dict[str, object]:
        return self._explorer().component(component_id)

    def component_graph(
        self,
        component_id: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self._explorer().component_graph(
            component_id,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def diagnostics(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage:
        return self._explorer().diagnostics(
            status=status,
            cursor=cursor,
            limit=limit,
        )

    def accepted_proposal(self, proposal_id: str) -> dict[str, object]:
        return self._explorer().edge(proposal_id)

    def search(self, query: str, top: int = 20) -> tuple[Node, ...]:
        return tuple(Node.model_validate(row) for row in search_nodes(self.out_dir, query, top))

    def nodes(self, top: int = 50) -> tuple[Node, ...]:
        rows = read_rows(
            self.out_dir,
            "nodes.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            ORDER BY event_slug, market_id, outcome_index
            LIMIT {int(top)}
            """,
        )
        return tuple(Node.model_validate(row) for row in rows)

    def edges(
        self,
        edge_type: Relation | None = None,
        top: int = 50,
    ) -> tuple[Edge, ...]:
        where = "WHERE edge_type = ?" if edge_type else ""
        rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            {where}
            ORDER BY edge_basis, edge_type, src_node_id, dst_node_id
            LIMIT {int(top)}
            """,
            [edge_type] if edge_type else None,
        )
        return tuple(Edge.model_validate(row) for row in rows)

    def condition(self, a: str, b: str) -> tuple[dict[str, object], ...]:
        a_id = self._resolve(a)
        b_id = self._resolve(b)
        return tuple(
            read_rows(
                self.out_dir,
                "conditional_edges.parquet",
                f"""
                SELECT * FROM read_parquet('{PATH_SENTINEL}')
                WHERE a_node_id = ? AND b_node_id = ?
                ORDER BY method, a_node_id, b_node_id
                """,
                [a_id, b_id],
            )
        )

    def explain_node(
        self,
        node: str,
        *,
        edge_limit: int | None = None,
    ) -> dict[str, object]:
        if edge_limit is not None and not 0 <= edge_limit <= 10_000:
            raise ValueError("edge_limit must be between 0 and 10000")
        node_id = self._resolve(node)
        node_rows = read_rows(
            self.out_dir,
            "nodes.parquet",
            f"SELECT * FROM read_parquet('{PATH_SENTINEL}') WHERE node_id = ?",
            [node_id],
        )
        limit_sql = f"LIMIT {edge_limit + 1}" if edge_limit is not None else ""
        touching_rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE src_node_id = ? OR dst_node_id = ?
            ORDER BY edge_type, src_node_id, dst_node_id
            {limit_sql}
            """,
            [node_id, node_id],
        )
        truncated = edge_limit is not None and len(touching_rows) > edge_limit
        touching = (
            touching_rows[:edge_limit]
            if edge_limit is not None
            else touching_rows
        )
        result: dict[str, object] = {"node": node_rows[0], "edges": touching}
        if edge_limit is not None:
            result["edges_truncated"] = truncated
        return result

    def explain_edge(self, src: str, dst: str, relation: Relation) -> dict[str, object]:
        src_id = self._resolve(src)
        dst_id = self._resolve(dst)
        symmetric = relation != "implies"
        where = (
            "((src_node_id = ? AND dst_node_id = ?) OR "
            "(src_node_id = ? AND dst_node_id = ?))"
            if symmetric
            else "src_node_id = ? AND dst_node_id = ?"
        )
        params: list[object] = [src_id, dst_id]
        if symmetric:
            params.extend((dst_id, src_id))
        params.append(relation)
        rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE {where} AND edge_type = ?
            ORDER BY confidence DESC, proposal_id
            """,
            params,
        )
        return {"src_node_id": src_id, "dst_node_id": dst_id, "relation": relation, "edges": rows}

    def prove(
        self,
        from_node: str,
        to_node: str,
        *,
        max_hops: int = 4,
        max_paths: int = 3,
    ) -> tuple[Proof, ...]:
        if not 1 <= max_hops <= _MAX_PROOF_HOPS:
            raise ValueError(
                f"max_hops must be between 1 and {_MAX_PROOF_HOPS}"
            )
        if not 1 <= max_paths <= _MAX_PROOF_PATHS:
            raise ValueError(
                f"max_paths must be between 1 and {_MAX_PROOF_PATHS}"
            )
        source = self._resolve(from_node)
        target = self._resolve(to_node)
        db = DuckDB(self.out_dir / "oddsfox_graph.duckdb", read_only=True)
        adjacency: dict[str, tuple[ProofStep, ...]] = {}

        def outgoing(node_id: str) -> tuple[ProofStep, ...]:
            cached = adjacency.get(node_id)
            if cached is not None:
                return cached
            rows = db.rows(
                """
                SELECT src_node_id, dst_node_id, edge_type, confidence, proposal_id
                FROM (
                    SELECT src_node_id, dst_node_id, edge_type,
                           confidence, proposal_id
                    FROM logic_edges_v
                    WHERE src_node_id = ?
                      AND edge_type IN ('implies', 'equivalent')
                    UNION ALL
                    SELECT dst_node_id AS src_node_id,
                           src_node_id AS dst_node_id,
                           edge_type, confidence, proposal_id
                    FROM logic_edges_v
                    WHERE dst_node_id = ? AND edge_type = 'equivalent'
                ) traversable
                ORDER BY dst_node_id, edge_type, proposal_id
                LIMIT ?
                """,
                [node_id, node_id, _MAX_PROOF_OUTGOING + 1],
            )
            if len(rows) > _MAX_PROOF_OUTGOING:
                raise ValueError(
                    f"Proof expansion for {node_id!r} exceeds the "
                    f"{_MAX_PROOF_OUTGOING}-edge safety cap"
                )
            result = tuple(ProofStep.model_validate(row) for row in rows)
            adjacency[node_id] = result
            return result
        counter = itertools.count()
        queue: list[
            tuple[
                int,
                float,
                tuple[str, ...],
                int,
                str,
                tuple[ProofStep, ...],
                frozenset[str],
            ]
        ] = [(0, -1.0, (source,), next(counter), source, (), frozenset({source}))]
        proofs: list[Proof] = []
        examined_states = 0
        generated_states = 1
        try:
            while queue and len(proofs) < max_paths:
                examined_states += 1
                if examined_states > _MAX_PROOF_STATES:
                    raise ValueError(
                        "Proof traversal exceeded the bounded search-state limit"
                    )
                hops, negative_bottleneck, path_nodes, _, node, path, visited = heapq.heappop(
                    queue
                )
                if node == target and path:
                    proofs.append(
                        Proof(
                            from_node_id=source,
                            to_node_id=target,
                            steps=path,
                            hops=hops,
                            bottleneck_confidence=-negative_bottleneck,
                        )
                    )
                    continue
                if hops >= max_hops:
                    continue
                for step in outgoing(node):
                    if step.dst_node_id in visited:
                        continue
                    next_path = (*path, step)
                    bottleneck = min(-negative_bottleneck, step.confidence)
                    generated_states += 1
                    if generated_states > _MAX_PROOF_STATES:
                        raise ValueError(
                            "Proof traversal exceeded the bounded search-state limit"
                        )
                    heapq.heappush(
                        queue,
                        (
                            hops + 1,
                            -bottleneck,
                            (*path_nodes, step.dst_node_id),
                            next(counter),
                            step.dst_node_id,
                            next_path,
                            visited | {step.dst_node_id},
                        ),
                    )
        finally:
            db.close()
        return tuple(proofs)

    def why_not(self, a: str, b: str, relation: Relation) -> Diagnostic:
        a_id = resolve_node(self.out_dir, a, require_unique=True)
        b_id = resolve_node(self.out_dir, b, require_unique=True)
        if a_id is None or b_id is None:
            return Diagnostic(status="unknown_node", relation=relation, explanation="One or both nodes do not exist")
        accepted = self.explain_edge(a_id, b_id, relation)["edges"]
        if accepted:
            candidates = _pair_rows(
                self.out_dir,
                "relation_candidates.parquet",
                a_id,
                b_id,
                None,
            )
            return Diagnostic.model_validate(
                {
                    "status": "accepted",
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "relation": relation,
                    "explanation": "The relation is published",
                    "provenance": accepted,
                    "candidate_reasons": (
                        candidates[0].get("candidate_reasons") if candidates else []
                    ),
                }
            )
        rejected = _pair_rows(self.out_dir, "rejected_edges.parquet", a_id, b_id, relation)
        if rejected:
            return Diagnostic.model_validate({"status": "solver_rejected", "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": str(rejected[0].get("rejection_reason") or "Rejected by solver"), "provenance": rejected})
        parse_quarantine = read_rows(
            self.out_dir,
            "quarantined_pairs.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE stage = 'parse' AND proposition_a_id IN (?, ?)
            ORDER BY quarantine_id
            LIMIT 20
            """,
            [a_id, b_id],
        )
        if parse_quarantine:
            return Diagnostic.model_validate(
                {
                    "status": "quarantined_parse",
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "relation": relation,
                    "explanation": str(
                        parse_quarantine[0].get("explanation")
                        or "One or both propositions failed parse consensus"
                    ),
                    "provenance": parse_quarantine,
                }
            )
        quarantined = _pair_rows(self.out_dir, "quarantined_pairs.parquet", a_id, b_id, None)
        if quarantined:
            reason = str(quarantined[0].get("reason_code") or "inference_failure")
            status_map = {
                "model_disagreement": "model_disagreement",
                "assumption": "assumption",
                "invalid_citation": "invalid_citation",
                "nli_veto": "nli_veto",
                "below_threshold": "below_threshold",
                "qualification_mismatch": "below_threshold",
                "inference_failure": "inference_failure",
                "authoritative_conflict": "quarantined_parse",
                "missing_model_parse": "quarantined_parse",
            }
            status = status_map.get(reason, "inference_failure")
            return Diagnostic.model_validate({"status": status, "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": str(quarantined[0].get("explanation") or reason), "provenance": quarantined})
        candidates = _pair_rows(self.out_dir, "relation_candidates.parquet", a_id, b_id, None)
        if candidates:
            if self.build_mode == "fast":
                return Diagnostic.model_validate(
                    {
                        "status": "not_applicable_to_deterministic_rules",
                        "proposition_a_id": a_id,
                        "proposition_b_id": b_id,
                        "relation": relation,
                        "explanation": "The pair has a deterministic proof, but not for the requested relation",
                        "candidate_reasons": candidates[0].get("candidate_reasons"),
                        "provenance": candidates,
                    }
                )
            candidate_status = str(candidates[0].get("status") or "")
            if candidate_status == "deadline_budget_exhausted":
                status = "deadline_budget_exhausted"
                explanation = "The pair was retrieved but the full-mode scheduling cutoff was reached"
            elif candidate_status == "not_classified_budget":
                status = "below_threshold"
                explanation = "The pair was retrieved but was outside the bounded inference budget"
            else:
                status = "below_threshold"
                explanation = "The pair was retrieved but did not produce the requested relation"
            return Diagnostic.model_validate({"status": status, "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": explanation, "candidate_reasons": candidates[0].get("candidate_reasons"), "provenance": candidates})
        if self.build_mode == "fast":
            if relation == "compatible":
                return Diagnostic(
                    status="full_mode_not_run",
                    proposition_a_id=a_id,
                    proposition_b_id=b_id,
                    relation=relation,
                    explanation="Fast mode does not run semantic consensus",
                )
            return Diagnostic(
                status="not_applicable_to_deterministic_rules",
                proposition_a_id=a_id,
                proposition_b_id=b_id,
                relation=relation,
                explanation="No exact deterministic rule applies to this pair",
            )
        return Diagnostic(status="not_retrieved", proposition_a_id=a_id, proposition_b_id=b_id, relation=relation, explanation="The pair was not generated by retrieval")

    def _resolve(self, text: str) -> str:
        result = resolve_node(self.out_dir, text, require_unique=True)
        if result is None:
            raise ValueError(f"Could not resolve node {text!r}")
        return result

    def _explorer(self) -> ExplorerStore:
        return ExplorerStore(self.out_dir)


def _pair_rows(
    out_dir: Path,
    artifact: str,
    a_id: str,
    b_id: str,
    relation: str | None,
) -> list[dict[str, object]]:
    if artifact == "relation_candidates.parquet":
        source_column, target_column = "proposition_a_id", "proposition_b_id"
        relation_column = "classification_relation"
    elif artifact == "quarantined_pairs.parquet":
        source_column, target_column = "proposition_a_id", "proposition_b_id"
        relation_column = "proposed_relation"
    else:
        source_column, target_column = "src_node_id", "dst_node_id"
        relation_column = "edge_type"
    symmetric = relation != "implies"
    pair_filter = f"{source_column} = ? AND {target_column} = ?"
    params: list[object] = [a_id, b_id]
    if symmetric:
        pair_filter = f"(({pair_filter}) OR ({source_column} = ? AND {target_column} = ?))"
        params.extend((b_id, a_id))
    relation_filter = f" AND {relation_column} = ?" if relation else ""
    if relation:
        params.append(relation)
    return read_rows(
        out_dir,
        artifact,
        f"""
        SELECT * FROM read_parquet('{PATH_SENTINEL}')
        WHERE {pair_filter}{relation_filter}
        LIMIT 20
        """,
        params,
    )
